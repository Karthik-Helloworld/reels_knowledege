import json
import shutil
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from edu.niche import NicheConfig
from edu.scripts.fact_check import fact_check, main as fact_main, package_parts
from edu.scripts.run_video import main as video_main
from edu.scripts.script_gen import ReelScript
from edu.sources.http import client_headers
from edu.sources.model import SourceDocument
from edu.tests.support import load_fixture, source_handler

RETRIEVED = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)
NICHE = NicheConfig(
    name="science & space facts",
    audience="general viewers",
    tone="clear and concrete",
    length_seconds=30,
    openstax_query="astronomy",
    ollama_host="http://127.0.0.1:11434",
    ollama_model="llama3.2",
    sources=("wikipedia",),
)


def check_files(package_name: str, llm_name: str):
    package = load_fixture(package_name)
    script, documents = package_parts(package)
    return fact_check(
        script,
        documents,
        niche=NICHE,
        llm_payload=load_fixture(llm_name),
    )


class FactCheckTests(unittest.TestCase):
    def test_pass_fixture(self) -> None:
        report = check_files("fact_package_pass.json", "fact_llm_pass.json")
        self.assertTrue(report.passed)
        self.assertFalse(report.blocked)
        self.assertTrue(report.fixture)
        self.assertEqual(report.model, "llama3.2")
        self.assertTrue(all(claim.passed for claim in report.claims))
        beat = next(claim for claim in report.claims if claim.id == "beat-0")
        self.assertEqual(beat.rule_failures, [])
        self.assertIn("384400", beat.source_text)
        self.assertIn("The Moon is Earth's only natural satellite.", beat.source_sentences)

    def test_low_support_fixture_fails(self) -> None:
        report = check_files("fact_package_low_support.json", "fact_llm_low_support.json")
        self.assertFalse(report.passed)
        self.assertTrue(report.blocked)
        beat = next(claim for claim in report.claims if claim.id == "beat-0")
        self.assertFalse(beat.llm_supported)
        self.assertIn("low support", beat.llm_reason)
        self.assertEqual(beat.rule_failures, [])
        self.assertFalse(beat.passed)
        hook = next(claim for claim in report.claims if claim.id == "hook")
        self.assertTrue(hook.passed)

    def test_number_date_and_name_must_be_in_the_source(self) -> None:
        report = check_files("fact_package_rules.json", "fact_llm_pass.json")
        self.assertFalse(report.passed)
        beat = next(claim for claim in report.claims if claim.id == "beat-0")
        self.assertTrue(beat.llm_supported)
        self.assertEqual(
            beat.rule_failures,
            [
                "number not in source: 12",
                "date not in source: April 2, 2022",
                "name not in source: Andrew Fraknoi",
            ],
        )
        self.assertFalse(beat.passed)

    def test_number_in_a_different_source_does_not_count(self) -> None:
        script = ReelScript.model_validate(
            {
                "topic": "Why the Moon has phases",
                "hook": "The Moon is Earth's only natural satellite.",
                "beats": [
                    {
                        "narration": "The distance is 12 kilometers.",
                        "on_screen": "distance",
                        "source_url": "https://en.wikipedia.org/wiki/Moon",
                    }
                ],
                "closing": "That is the Moon.",
                "key_takeaway": "The Moon is Earth's only natural satellite.",
                "cta": "Look up tonight.",
                "claims": [
                    {
                        "text": "The Moon is Earth's only natural satellite.",
                        "source_url": "https://en.wikipedia.org/wiki/Moon",
                        "source_sentence": "The Moon is Earth's only natural satellite.",
                    }
                ],
            }
        )
        documents = [
            SourceDocument(
                title="Moon",
                text="The Moon is Earth's only natural satellite.",
                url="https://en.wikipedia.org/wiki/Moon",
                license="CC BY-SA 4.0",
                author="Wikipedia contributors",
                retrieved_at=RETRIEVED,
            ),
            SourceDocument(
                title="Other",
                text="The distance is 12 kilometers.",
                url="https://example.com/other",
                license="CC BY 4.0",
                author="Ada",
                retrieved_at=RETRIEVED,
            ),
        ]
        report = fact_check(
            script,
            documents,
            niche=NICHE,
            llm_payload=load_fixture("fact_llm_pass.json"),
        )
        beat = next(claim for claim in report.claims if claim.id == "beat-0")
        self.assertIn("number not in source: 12", beat.rule_failures)
        self.assertFalse(report.passed)

    def test_second_pass_posts_the_niche_model_and_source_sentence(self) -> None:
        seen: dict = {}

        def handler(request: httpx.Request) -> httpx.Response:
            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content.decode())
            script = load_fixture("fact_llm_pass.json")
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": json.dumps(script)}},
            )

        package = load_fixture("fact_package_pass.json")
        script, documents = package_parts(package)
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            report = fact_check(script, documents, niche=NICHE, client=client)
        self.assertTrue(report.passed)
        self.assertFalse(report.fixture)
        self.assertTrue(seen["url"].endswith("/api/chat"))
        self.assertEqual(seen["body"]["model"], "llama3.2")
        self.assertIs(seen["body"]["stream"], False)
        self.assertEqual(seen["body"]["format"], "json")
        user = seen["body"]["messages"][1]["content"]
        self.assertIn("The Moon is Earth's only natural satellite.", user)
        self.assertIn("https://en.wikipedia.org/wiki/Moon", user)
        self.assertIn("cited source sentences", seen["body"]["messages"][0]["content"])

    def test_dry_run_fixture_exits_zero_on_pass_and_nonzero_on_low_support(self) -> None:
        with TemporaryDirectory() as tmp:
            report_path = Path(tmp) / "fact_report.json"
            code = fact_main(
                [
                    "--package",
                    str(Path("edu/tests/fixtures/fact_package_pass.json")),
                    "--llm-fixture",
                    str(Path("edu/tests/fixtures/fact_llm_pass.json")),
                    "--report",
                    str(report_path),
                ]
            )
            saved = json.loads(report_path.read_text(encoding="utf-8"))
            self.assertEqual(code, 0)
            self.assertTrue(saved["passed"])
            self.assertTrue(saved["fixture"])
            fail_code = fact_main(
                [
                    "--package",
                    str(Path("edu/tests/fixtures/fact_package_low_support.json")),
                    "--llm-fixture",
                    str(Path("edu/tests/fixtures/fact_llm_low_support.json")),
                    "--report",
                    str(report_path),
                ]
            )
            failed = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(fail_code, 1)
        self.assertFalse(failed["passed"])
        beat = next(claim for claim in failed["claims"] if claim["id"] == "beat-0")
        self.assertFalse(beat["passed"])
        self.assertIn("low support", beat["llm_reason"])

    def test_failed_claim_blocks_edu_video(self) -> None:
        calls = {"n": 0}

        def ollama_handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            script = load_fixture("ollama_script.json")
            body = {"message": {"role": "assistant", "content": json.dumps(script)}}
            return httpx.Response(200, json=body)

        source = httpx.Client(
            transport=httpx.MockTransport(source_handler),
            headers=client_headers(),
        )
        ollama = httpx.Client(transport=httpx.MockTransport(ollama_handler))
        with TemporaryDirectory() as tmp:
            script_path = Path(tmp) / "video.json"
            report_path = Path(tmp) / "fact_report.json"
            with patch(
                "edu.scripts.run_video.make_client",
                side_effect=[source, ollama],
            ):
                code = video_main(
                    [
                        "--topic",
                        "Why the Moon has phases",
                        "--out",
                        str(script_path),
                        "--report",
                        str(report_path),
                        "--fact-fixture",
                        str(Path("edu/tests/fixtures/fact_llm_low_support.json")),
                    ]
                )
            self.assertEqual(code, 1)
            self.assertFalse(script_path.exists())
            self.assertEqual(calls["n"], 1)
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertFalse(report["passed"])
        self.assertTrue(report["blocked"])
        beat = next(claim for claim in report["claims"] if claim["id"] == "beat-0")
        self.assertFalse(beat["passed"])
        self.assertFalse(beat["llm_supported"])
        self.assertIn("low support", beat["llm_reason"])

    def test_passing_fact_check_keeps_the_package(self) -> None:
        def ollama_handler(request: httpx.Request) -> httpx.Response:
            script = load_fixture("ollama_script.json")
            body = {"message": {"role": "assistant", "content": json.dumps(script)}}
            return httpx.Response(200, json=body)

        source = httpx.Client(
            transport=httpx.MockTransport(source_handler),
            headers=client_headers(),
        )
        ollama = httpx.Client(transport=httpx.MockTransport(ollama_handler))
        with TemporaryDirectory() as tmp:
            script_path = Path(tmp) / "video.json"
            report_path = Path(tmp) / "fact_report.json"
            topics_path = Path(tmp) / "topics.csv"
            shutil.copyfile(Path("edu/topics.csv"), topics_path)
            with patch(
                "edu.scripts.run_video.make_client",
                side_effect=[source, ollama],
            ):
                code = video_main(
                    [
                        "--topic",
                        "Why the Moon has phases",
                        "--out",
                        str(script_path),
                        "--report",
                        str(report_path),
                        "--topics",
                        str(topics_path),
                        "--fact-fixture",
                        str(Path("edu/tests/fixtures/fact_llm_pass.json")),
                    ]
                )
            self.assertEqual(code, 0)
            package = json.loads(script_path.read_text(encoding="utf-8"))
            report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertTrue(package["fact_check"]["passed"])
        self.assertEqual(package["fact_check"]["report"], "fact_report.json")
        self.assertTrue(report["passed"])
        self.assertFalse(report["blocked"])


if __name__ == "__main__":
    unittest.main()
