import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

import httpx

from edu.scripts.run_video import build_package, topic_slug, write_package
from edu.scripts.script_gen import GroundingError
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


if __name__ == "__main__":
    unittest.main()
