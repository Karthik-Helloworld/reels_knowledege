import json
import os
import shutil
import unittest
import wave
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from edu.scripts.fact_check import fact_check, package_parts
from edu.scripts.render_reel import write_tone
from edu.scripts.run_video import main as video_main
from edu.scripts.scene_plan import (
    CREDIT_SECONDS,
    SAFE_BOTTOM,
    SAFE_RIGHT,
    TARGET_MAX_SECONDS,
    TARGET_MIN_SECONDS,
    TARGET_SPOKEN_SECONDS,
    build_props,
    credit_line,
    even_captions,
    hashtags,
    ken_burns_source,
    post_caption,
    scene_spans,
    spoken_seconds,
)
from edu.tests.support import load_fixture
from edu.tests.test_fact_check import NICHE


class ScenePlanTests(unittest.TestCase):
    def test_source_credit_is_two_seconds_after_the_audio(self) -> None:
        spans = scene_spans(TARGET_SPOKEN_SECONDS)
        ids = [span["id"] for span in spans]
        self.assertEqual(
            ids,
            ["hook", "concept", "diagram", "timeline", "stat", "cta", "sourceCredit"],
        )
        spoken_frames = sum(span["duration"] for span in spans if span["id"] != "sourceCredit")
        credit = spans[-1]
        self.assertEqual(credit["id"], "sourceCredit")
        self.assertEqual(credit["from"], spoken_frames)
        self.assertEqual(credit["duration"], 60)
        self.assertEqual(spoken_frames, 36 * 30)
        self.assertEqual(CREDIT_SECONDS, 2.0)

    def test_tone_length_is_inside_the_target(self) -> None:
        spoken = spoken_seconds(None, "tone")
        total = spoken + CREDIT_SECONDS
        self.assertEqual(spoken, TARGET_SPOKEN_SECONDS)
        self.assertGreaterEqual(total, TARGET_MIN_SECONDS)
        self.assertLessEqual(total, TARGET_MAX_SECONDS)
        self.assertEqual(spoken_seconds(12.0, "piper"), TARGET_SPOKEN_SECONDS)
        self.assertEqual(spoken_seconds(40.0, "piper"), 40.0)

    def test_caption_insets_match_the_scene_constants(self) -> None:
        safe = Path("edu/remotion/safe.ts").read_text(encoding="utf-8")
        self.assertIn(f"export const SAFE_BOTTOM = {SAFE_BOTTOM}", safe)
        self.assertIn(f"export const SAFE_RIGHT = {SAFE_RIGHT}", safe)
        captions = Path("edu/remotion/Captions.tsx").read_text(encoding="utf-8")
        self.assertIn("bottom: SAFE_BOTTOM", captions)
        self.assertIn("right: SAFE_RIGHT", captions)

    def test_hashtags_and_attribution(self) -> None:
        package = load_fixture("render_package.json")
        tags = hashtags(package["topic"])
        self.assertGreaterEqual(len(tags), 5)
        self.assertLessEqual(len(tags), 8)
        self.assertTrue(all(tag.startswith("#") for tag in tags))
        caption = post_caption(package)
        source = package["sources"][0]
        self.assertIn(source["title"], caption)
        self.assertIn(source["url"], caption)
        self.assertIn(source["license"], caption)
        self.assertIn(source["author"], caption)
        self.assertIn(" ".join(tags), caption)
        self.assertEqual(
            credit_line(source["author"], source["license"]),
            "Source: Wikipedia contributors, CC BY-SA 4.0",
        )

    def test_ken_burns_only_for_reusable_nasa_and_commons_images(self) -> None:
        commons = {
            "title": "Full Moon",
            "url": "https://commons.wikimedia.org/wiki/File:Full_Moon.jpg",
            "license": "CC BY-SA 4.0",
            "image_url": "https://upload.wikimedia.org/wikipedia/commons/full-moon.jpg",
        }
        self.assertEqual(ken_burns_source(commons)["url"], commons["image_url"])
        blocked = dict(commons, license="CC BY-NC 4.0")
        self.assertIsNone(ken_burns_source(blocked))
        nasa = {
            "title": "Nearside of the Moon",
            "url": "https://images.nasa.gov/details/PIA12235",
            "license": "Public domain",
            "image_url": "https://images-assets.nasa.gov/image/PIA12235/PIA12235~thumb.jpg",
        }
        self.assertIsNotNone(ken_burns_source(nasa))
        self.assertIsNone(ken_burns_source(dict(nasa, license="Creative Commons Attribution 4.0")))
        article = dict(
            commons,
            url="https://en.wikipedia.org/wiki/Moon",
            image_url="https://upload.wikimedia.org/wikipedia/commons/full-moon.jpg",
        )
        # The page host is Wikipedia, but the file host is Wikimedia, so reuse is allowed.
        self.assertIsNotNone(ken_burns_source(article))
        self.assertIsNone(ken_burns_source(dict(commons, image_url=None)))

    def test_even_captions_stay_inside_the_spoken_window(self) -> None:
        cues = even_captions("Light cannot leave", 36.0)
        self.assertEqual([cue["text"] for cue in cues], ["Light", "cannot", "leave"])
        self.assertEqual(cues[0]["start"], 0.0)
        self.assertLessEqual(cues[-1]["end"], 36.0)
        self.assertGreater(cues[-1]["end"], 35.0)

    def test_props_name_every_scene_and_the_credit_line(self) -> None:
        package = load_fixture("render_package.json")
        props = build_props(
            package,
            spoken=36.0,
            audio_file="voice.wav",
            image_file=None,
            captions=even_captions("Light", 36.0),
        )
        self.assertEqual(
            [scene["id"] for scene in props["scenes"]],
            ["hook", "concept", "diagram", "timeline", "stat", "cta", "sourceCredit"],
        )
        self.assertEqual(props["creditSeconds"], 2.0)
        self.assertEqual(
            props["credits"][0]["line"],
            "Source: Wikipedia contributors, CC BY-SA 4.0",
        )
        self.assertIsNone(props["imageFile"])
        self.assertEqual(props["audioFile"], "voice.wav")

    def test_render_fixture_passes_fact_check(self) -> None:
        package = load_fixture("render_package.json")
        script, documents = package_parts(package)
        report = fact_check(
            script,
            documents,
            niche=NICHE,
            llm_payload=load_fixture("render_llm_pass.json"),
        )
        self.assertTrue(report.passed)
        self.assertTrue(all(claim.passed for claim in report.claims))

    def test_tone_wav_has_duration(self) -> None:
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "voice.wav"
            duration = write_tone(path, 0.2)
            with wave.open(str(path), "rb") as handle:
                self.assertEqual(handle.getnchannels(), 1)
                self.assertGreater(handle.getnframes(), 0)
            self.assertGreater(duration, 0.15)
            self.assertLess(duration, 0.25)

    def test_ollama_failure_renders_the_fixture_without_marking_topics(self) -> None:
        from edu.scripts.script_gen import GroundingError

        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "black-hole.json"
            report = Path(tmp) / "fact_report.json"
            topics = Path(tmp) / "topics.csv"
            shutil.copyfile(Path("edu/topics.csv"), topics)
            before = topics.read_text(encoding="utf-8")
            video = Path(tmp) / "black-hole.mp4"
            caption = Path(tmp) / "black-hole.caption.txt"
            pending = Path(tmp) / "pending"

            def fake_render(package_path: Path) -> tuple[Path, Path]:
                del package_path
                video.write_bytes(b"fake-mp4")
                caption.write_text("Black hole\n")
                return video, caption

            with patch.dict(os.environ, {"EDU_PENDING_DIR": str(pending)}), patch(
                "edu.scripts.run_video.complete_video",
                side_effect=GroundingError("Ollama returned no JSON object"),
            ), patch(
                "edu.scripts.run_video.render_reel",
                side_effect=fake_render,
            ) as render:
                code = video_main(
                    [
                        "--topic",
                        "Black hole",
                        "--out",
                        str(out),
                        "--report",
                        str(report),
                        "--topics",
                        str(topics),
                    ]
                )
            self.assertEqual(code, 0)
            render.assert_called_once()
            bundle = pending / "black-hole"
            self.assertTrue((bundle / "video.mp4").is_file())
            self.assertTrue((bundle / "script.json").is_file())
            self.assertTrue((bundle / "claims.json").is_file())
            self.assertTrue((bundle / "fact_report.json").is_file())
            self.assertTrue((bundle / "caption.txt").is_file())
            self.assertTrue((bundle / "sources.json").is_file())
            self.assertFalse(out.exists())
            self.assertFalse(report.exists())
            self.assertFalse(video.exists())
            self.assertFalse(caption.exists())
            script = json.loads((bundle / "script.json").read_text(encoding="utf-8"))
            stored = json.loads((bundle / "fact_report.json").read_text(encoding="utf-8"))
            self.assertEqual(script["topic"], "Black hole")
            self.assertTrue(stored["passed"])
            self.assertEqual(topics.read_text(encoding="utf-8"), before)

    def test_blank_closing_exits_instead_of_the_fixture(self) -> None:
        from edu.scripts.script_gen import GroundingError

        error = GroundingError(
            "script JSON did not match the schema: closing\n"
            "  String should have at least 1 character [type=string_too_short]"
        )
        with patch(
            "edu.scripts.run_video.complete_video",
            side_effect=error,
        ), patch("edu.scripts.run_video.write_fixture_package") as fixture, patch(
            "edu.scripts.run_video.render_reel"
        ) as render:
            code = video_main(["--topic", "Black hole", "--no-render"])
        self.assertEqual(code, 1)
        fixture.assert_not_called()
        render.assert_not_called()

        blank_takeaway = GroundingError(
            "script JSON did not match the schema: key_takeaway\n"
            "  Value error, must not be blank [type=value_error]"
        )
        with patch(
            "edu.scripts.run_video.complete_video",
            side_effect=blank_takeaway,
        ), patch("edu.scripts.run_video.write_fixture_package") as takeaway_fixture:
            code = video_main(["--topic", "Black hole", "--no-render"])
        self.assertEqual(code, 1)
        takeaway_fixture.assert_not_called()

    def test_live_run_can_refuse_the_fixture(self) -> None:
        from edu.scripts.script_gen import GroundingError

        error = GroundingError("hook uses words that are not in the cited sentence: Imagine")
        with patch.dict(os.environ, {"EDU_NO_FIXTURE": "1"}), patch(
            "edu.scripts.run_video.complete_video",
            side_effect=error,
        ), patch("edu.scripts.run_video.write_fixture_package") as fixture:
            code = video_main(["--topic", "Black hole", "--no-render"])
        self.assertEqual(code, 1)
        fixture.assert_not_called()


if __name__ == "__main__":
    unittest.main()
