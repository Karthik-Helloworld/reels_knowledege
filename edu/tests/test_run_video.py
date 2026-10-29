import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from edu.scripts.fact_check import ClaimReport, FactCheckError, FactReport
from edu.scripts.run_video import build_package, complete_video, topic_slug, write_package
from edu.scripts.script_gen import OLLAMA_ATTEMPTS, GroundingError
from edu.sources.http import client_headers
from edu.tests.support import load_fixture, source_handler

RETRIEVED = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


class RunVideoTests(unittest.TestCase):
    def test_slug(self) -> None:
        self.assertEqual(topic_slug("Why the Moon has phases"), "why-the-moon-has-phases")

    def test_package_uses_mock_http_and_the_ollama_fixture(self) -> None:
        def ollama_handler(request: httpx.Request) -> httpx.Response:
            self.assertTrue(str(request.url).endswith("/api/chat"))
            script = load_fixture("ollama_script.json")
            body = {"message": {"role": "assistant", "content": json.dumps(script)}}
            return httpx.Response(200, json=body)

        with httpx.Client(
            transport=httpx.MockTransport(source_handler),
            headers=client_headers(),
        ) as source_client, httpx.Client(
            transport=httpx.MockTransport(ollama_handler)
        ) as ollama_client:
            package = build_package(
                "Why the Moon has phases",
                source_client=source_client,
                ollama_client=ollama_client,
                retrieved_at=RETRIEVED,
            )
        self.assertEqual(package["niche"], "science & space facts")
        self.assertEqual(package["topic"], "Why the Moon has phases")
        self.assertIn("Moon", package["angle"])
        self.assertEqual(
            [item["title"] for item in package["sources"]],
            ["Moon", "Nearside of the Moon", "Astronomy 2e", "Full Moon"],
        )
        self.assertEqual(
            package["script"]["beats"][0]["source_url"],
            "https://en.wikipedia.org/wiki/Moon",
        )
        self.assertTrue(package["sources"][0]["retrieved_at"].startswith("2026-10-28T12:00:00"))
        with TemporaryDirectory() as tmp:
            path = write_package(package, Path(tmp) / "nested" / "script.json")
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(saved["script"]["hook"], package["script"]["hook"])

    def test_no_sources_raises(self) -> None:
        def empty_handler(request: httpx.Request) -> httpx.Response:
            host = request.url.host
            if host == "en.wikipedia.org":
                return httpx.Response(200, json={"query": {"search": []}})
            if host == "images-api.nasa.gov":
                return httpx.Response(200, json={"collection": {"items": []}})
            if host == "openstax.org":
                return httpx.Response(200, json={"items": []})
            if host == "commons.wikimedia.org":
                return httpx.Response(200, json={"query": {"pages": {}}})
            return httpx.Response(404)

        with httpx.Client(
            transport=httpx.MockTransport(empty_handler)
        ) as source_client, httpx.Client(
            transport=httpx.MockTransport(empty_handler)
        ) as ollama_client:
            with self.assertRaises(GroundingError):
                build_package(
                    "zzzz-no-such-topic",
                    source_client=source_client,
                    ollama_client=ollama_client,
                    retrieved_at=RETRIEVED,
                )

    def test_fact_check_rejection_is_retried_until_a_script_passes(self) -> None:
        calls = {"n": 0}

        def fake_fact_check(*_args, **_kwargs) -> FactReport:
            calls["n"] += 1
            passed = calls["n"] > 1
            return FactReport(
                passed=passed,
                blocked=not passed,
                model="llama3.2",
                fixture=False,
                claims=[
                    ClaimReport(
                        id="hook",
                        claim="The Moon is Earth's only natural satellite.",
                        source_urls=["https://en.wikipedia.org/wiki/Moon"],
                        source_text="The Moon is Earth's only natural satellite.",
                        source_sentences=["The Moon is Earth's only natural satellite."],
                        llm_supported=passed,
                        llm_reason="supported" if passed else "added Imagine",
                        rule_failures=[] if passed else ["name not in source: Imagine"],
                        passed=passed,
                    )
                ],
            )

        def ollama_handler(_request: httpx.Request) -> httpx.Response:
            script = load_fixture("ollama_script.json")
            body = {"message": {"role": "assistant", "content": json.dumps(script)}}
            return httpx.Response(200, json=body)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            topics = root / "topics.csv"
            topics.write_text(
                "topic,angle,status\nWhy the Moon has phases,orbit,pending\n",
                encoding="utf-8",
            )
            with httpx.Client(
                transport=httpx.MockTransport(source_handler),
                headers=client_headers(),
            ) as source_client, httpx.Client(
                transport=httpx.MockTransport(ollama_handler)
            ) as ollama_client, patch(
                "edu.scripts.run_video.fact_check",
                side_effect=fake_fact_check,
            ):
                path = complete_video(
                    "Why the Moon has phases",
                    source_client=source_client,
                    ollama_client=ollama_client,
                    topics_path=topics,
                    retrieved_at=RETRIEVED,
                    out_path=root / "moon.json",
                    report_path=root / "fact_report.json",
                )
            saved = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(calls["n"], 2)
        self.assertTrue(saved["fact_check"]["passed"])

    def test_exhausted_fact_check_budget_keeps_the_report(self) -> None:
        calls = {"n": 0}

        def always_fail(*_args, **_kwargs) -> FactReport:
            calls["n"] += 1
            return FactReport(
                passed=False,
                blocked=True,
                model="llama3.2",
                fixture=False,
                claims=[
                    ClaimReport(
                        id="hook",
                        claim="Imagine the Moon.",
                        source_urls=["https://en.wikipedia.org/wiki/Moon"],
                        source_text="The Moon is Earth's only natural satellite.",
                        source_sentences=["The Moon is Earth's only natural satellite."],
                        llm_supported=False,
                        llm_reason="added Imagine",
                        rule_failures=["name not in source: Imagine"],
                        passed=False,
                    )
                ],
            )

        def ollama_handler(_request: httpx.Request) -> httpx.Response:
            script = load_fixture("ollama_script.json")
            body = {"message": {"role": "assistant", "content": json.dumps(script)}}
            return httpx.Response(200, json=body)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            topics = root / "topics.csv"
            topics.write_text(
                "topic,angle,status\nWhy the Moon has phases,orbit,pending\n",
                encoding="utf-8",
            )
            report = root / "fact_report.json"
            with httpx.Client(
                transport=httpx.MockTransport(source_handler),
                headers=client_headers(),
            ) as source_client, httpx.Client(
                transport=httpx.MockTransport(ollama_handler)
            ) as ollama_client, patch(
                "edu.scripts.run_video.fact_check",
                side_effect=always_fail,
            ):
                with self.assertRaises(FactCheckError) as caught:
                    complete_video(
                        "Why the Moon has phases",
                        source_client=source_client,
                        ollama_client=ollama_client,
                        topics_path=topics,
                        retrieved_at=RETRIEVED,
                        out_path=root / "moon.json",
                        report_path=report,
                    )
            saved = json.loads(report.read_text(encoding="utf-8"))
        self.assertIn("Imagine", str(caught.exception))
        self.assertFalse(saved["passed"])
        self.assertEqual(calls["n"], OLLAMA_ATTEMPTS)

    def test_fact_check_retry_keeps_the_first_topic(self) -> None:
        class Sequenced:
            def __init__(self) -> None:
                self.calls = 0

            def choice(self, items):
                item = items[self.calls % len(items)]
                self.calls += 1
                return item

        rng = Sequenced()
        seen: list[str] = []

        def fake_fact_check(*_args, **_kwargs) -> FactReport:
            passed = len(seen) > 1
            return FactReport(
                passed=passed,
                blocked=not passed,
                model="qwen2.5:7b",
                fixture=False,
                claims=[
                    ClaimReport(
                        id="hook",
                        claim="The Moon is Earth's only natural satellite.",
                        source_urls=["https://en.wikipedia.org/wiki/Moon"],
                        source_text="The Moon is Earth's only natural satellite.",
                        source_sentences=["The Moon is Earth's only natural satellite."],
                        llm_supported=passed,
                        llm_reason="supported" if passed else "retry",
                        rule_failures=[],
                        passed=passed,
                    )
                ],
            )

        def ollama_handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content.decode())
            user = body["messages"][1]["content"]
            payload = json.loads(user.split("\n", 1)[1])
            topic = payload["topic"]
            seen.append(topic)
            script = load_fixture("ollama_script.json")
            script["topic"] = topic
            return httpx.Response(
                200,
                json={"message": {"role": "assistant", "content": json.dumps(script)}},
            )

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            topics = root / "topics.csv"
            topics.write_text(
                "topic,angle,status\nAlpha,first angle,pending\nBeta,second angle,pending\n",
                encoding="utf-8",
            )
            with httpx.Client(
                transport=httpx.MockTransport(source_handler),
                headers=client_headers(),
            ) as source_client, httpx.Client(
                transport=httpx.MockTransport(ollama_handler)
            ) as ollama_client, patch(
                "edu.scripts.run_video.fact_check",
                side_effect=fake_fact_check,
            ):
                path = complete_video(
                    "",
                    source_client=source_client,
                    ollama_client=ollama_client,
                    topics_path=topics,
                    retrieved_at=RETRIEVED,
                    rng=rng,
                    out_path=root / "alpha.json",
                    report_path=root / "fact_report.json",
                )
            saved = json.loads(path.read_text(encoding="utf-8"))
            catalog = topics.read_text(encoding="utf-8")
        self.assertEqual(rng.calls, 1)
        self.assertEqual(seen, ["Alpha", "Alpha"])
        self.assertEqual(saved["topic"], "Alpha")
        self.assertIn("Alpha,first angle,used", catalog)
        self.assertIn("Beta,second angle,pending", catalog)


if __name__ == "__main__":
    unittest.main()
