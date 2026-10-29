import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from edu.scripts.pending import format_item, stage_pending
from edu.scripts.queue import can_publish, connect, get_item, list_decisions, list_queue
from edu.scripts.review import main as review_main

NOW = "2026-10-28T15:00:00+00:00"
NOW_DT = datetime(2026, 10, 28, 15, 0, tzinfo=timezone.utc)


def write_package(directory: Path, name: str, topic: str) -> tuple[Path, Path, Path, Path]:
    package_path = directory / f"{name}.json"
    video_path = directory / f"{name}.mp4"
    caption_path = directory / f"{name}.caption.txt"
    report_path = directory / "fact_report.json"
    script = {
        "topic": topic,
        "hook": f"{topic} starts here.",
        "beats": [
            {
                "narration": f"{topic} is the example.",
                "on_screen": "example",
                "source_url": "https://en.wikipedia.org/wiki/Example",
            }
        ],
        "closing": "That is the example.",
        "key_takeaway": f"{topic} is the example.",
        "cta": "Follow for another science fact.",
        "claims": [
            {
                "text": f"{topic} is the example.",
                "source_url": "https://en.wikipedia.org/wiki/Example",
                "source_sentence": f"{topic} is the example.",
            }
        ],
    }
    package_path.write_text(
        json.dumps({"topic": topic, "script": script}) + "\n",
        encoding="utf-8",
    )
    video_path.write_bytes(b"fake-mp4")
    caption_path.write_text(f"{topic}\n\n#ScienceFacts\n", encoding="utf-8")
    report_path.write_text(
        json.dumps(
            {
                "passed": True,
                "blocked": False,
                "claims": [
                    {
                        "id": "hook",
                        "passed": True,
                        "llm_reason": "The cited source states this.",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return package_path, video_path, caption_path, report_path


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.pending = self.root / "pending"
        self.approved = self.root / "approved"
        self.rejected = self.root / "rejected"
        self.db = self.root / "queue.sqlite"
        self.pending.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def stage(self, name: str, topic: str) -> Path:
        package, video, caption, report = write_package(self.root, name, topic)
        return stage_pending(
            package,
            video,
            caption,
            report,
            root=self.pending,
        )

    def review(self, *args: str) -> int:
        return review_main(
            [
                "--pending",
                str(self.pending),
                "--approved",
                str(self.approved),
                "--rejected",
                str(self.rejected),
                "--db",
                str(self.db),
                "--now",
                NOW,
                "--cadence-per-day",
                "1",
                *args,
            ]
        )

    def test_pending_bundle_has_video_script_claims_report_and_caption(self) -> None:
        bundle = self.stage("black-hole", "Black hole")
        shown = format_item(bundle)
        self.assertTrue((bundle / "video.mp4").is_file())
        self.assertTrue((bundle / "sources.json").is_file())
        self.assertIn("Black hole starts here.", shown)
        self.assertIn("Black hole is the example.", shown)
        self.assertIn("https://en.wikipedia.org/wiki/Example", shown)
        self.assertIn("passed: True", shown)
        self.assertIn("#ScienceFacts", shown)
        self.assertIn(str(bundle / "video.mp4"), shown)
        connection = connect(self.db)
        try:
            self.assertFalse(can_publish(connection, "black-hole"))
            self.assertEqual(list_queue(connection), [])
        finally:
            connection.close()

    def test_approve_enters_the_queue_and_reject_does_not(self) -> None:
        self.stage("black-hole", "Black hole")
        self.stage("mars", "Mars")
        self.assertEqual(self.review("--decide", "approve", "--id", "black-hole"), 0)
        self.assertEqual(self.review("--decide", "reject", "--id", "mars"), 0)
        self.assertFalse((self.pending / "black-hole").exists())
        self.assertFalse((self.pending / "mars").exists())
        self.assertTrue((self.approved / "black-hole" / "video.mp4").is_file())
        self.assertTrue((self.rejected / "mars" / "fact_report.json").is_file())
        connection = connect(self.db)
        try:
            rows = list_queue(connection)
            self.assertEqual([row["id"] for row in rows], ["black-hole"])
            self.assertEqual(rows[0]["status"], "approved")
            self.assertEqual(rows[0]["scheduled_at"], "2026-10-28T15:00:00+00:00")
            self.assertTrue(can_publish(connection, "black-hole"))
            self.assertFalse(can_publish(connection, "mars"))
            self.assertIsNone(get_item(connection, "mars"))
            decisions = list_decisions(connection)
            self.assertEqual([row["action"] for row in decisions], ["approve", "reject"])
            self.assertEqual(decisions[0]["actor"], "local")
            self.assertEqual(decisions[1]["item_id"], "mars")
            self.assertIn("rejected", decisions[1]["detail"])
        finally:
            connection.close()

    def test_edit_changes_the_caption_and_does_not_enqueue(self) -> None:
        bundle = self.stage("black-hole", "Black hole")
        code = self.review(
            "--decide",
            "edit",
            "--id",
            "black-hole",
            "--caption",
            "Revised caption for review",
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            (bundle / "caption.txt").read_text(encoding="utf-8"),
            "Revised caption for review\n",
        )
        self.assertIn("Revised caption for review", format_item(bundle))
        connection = connect(self.db)
        try:
            self.assertEqual(list_queue(connection), [])
            self.assertFalse(can_publish(connection, "black-hole"))
            decisions = list_decisions(connection, "black-hole")
            self.assertEqual(len(decisions), 1)
            self.assertEqual(decisions[0]["action"], "edit")
            detail = json.loads(decisions[0]["detail"])
            self.assertIn("#ScienceFacts", detail["previous_caption"])
            self.assertEqual(detail["caption"], "Revised caption for review\n")
        finally:
            connection.close()

    def test_second_approval_waits_one_day(self) -> None:
        self.stage("black-hole", "Black hole")
        self.stage("mars", "Mars")
        self.assertEqual(self.review("--decide", "approve", "--id", "black-hole"), 0)
        self.assertEqual(self.review("--decide", "approve", "--id", "mars"), 0)
        connection = connect(self.db)
        try:
            rows = list(list_queue(connection))
        finally:
            connection.close()
        first = datetime.fromisoformat(rows[0]["scheduled_at"])
        second = datetime.fromisoformat(rows[1]["scheduled_at"])
        self.assertEqual(second - first, timedelta(days=1))
        self.assertEqual(first, NOW_DT)

    def test_two_per_day_spaces_approvals_by_twelve_hours(self) -> None:
        self.stage("black-hole", "Black hole")
        self.stage("mars", "Mars")
        common = [
            "--pending",
            str(self.pending),
            "--approved",
            str(self.approved),
            "--rejected",
            str(self.rejected),
            "--db",
            str(self.db),
            "--now",
            NOW,
            "--cadence-per-day",
            "2",
        ]
        self.assertEqual(review_main([*common, "--decide", "approve", "--id", "black-hole"]), 0)
        self.assertEqual(review_main([*common, "--decide", "approve", "--id", "mars"]), 0)
        connection = connect(self.db)
        try:
            rows = list(list_queue(connection))
        finally:
            connection.close()
        first = datetime.fromisoformat(rows[0]["scheduled_at"])
        second = datetime.fromisoformat(rows[1]["scheduled_at"])
        self.assertEqual(second - first, timedelta(hours=12))

    def test_non_interactive_flag_is_required_without_a_tty(self) -> None:
        self.stage("black-hole", "Black hole")
        with patch("sys.stdin.isatty", return_value=False):
            code = review_main(
                [
                    "--pending",
                    str(self.pending),
                    "--db",
                    str(self.db),
                ]
            )
        self.assertEqual(code, 2)
        self.assertTrue((self.pending / "black-hole").is_dir())
        connection = connect(self.db)
        try:
            self.assertFalse(can_publish(connection, "black-hole"))
        finally:
            connection.close()

    def test_default_cadence_is_one_per_day(self) -> None:
        with patch.dict(os.environ, {"REVIEW_CADENCE_PER_DAY": ""}, clear=False):
            from edu.scripts.queue import configured_cadence

            self.assertEqual(configured_cadence(), 1.0)
        with patch.dict(os.environ, {"REVIEW_CADENCE_PER_DAY": "3"}):
            from edu.scripts.queue import configured_cadence

            self.assertEqual(configured_cadence(), 3.0)


if __name__ == "__main__":
    unittest.main()
