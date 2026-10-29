import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import httpx

from edu.scripts.render_reel import stage_image
from edu.sources.http import client_headers


def _jpeg() -> bytes:
    return b"\xff\xd8\xff" + b"x" * 40


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers=client_headers())


NASA = {
    "title": "Nearside of the Moon",
    "url": "https://images.nasa.gov/details/PIA12235",
    "license": "Public domain",
    "image_url": "https://images-assets.nasa.gov/image/PIA12235/preview.jpg",
}


class StageImageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.directory = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_downloads_a_licensed_still_with_the_injected_client(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})

        with _client(handler) as client:
            name = stage_image(NASA, self.directory, client)
        self.assertEqual(name, "ken-burns.jpg")
        written = self.directory / "ken-burns.jpg"
        self.assertEqual(written.read_bytes(), _jpeg())
        self.assertEqual(len(seen), 1)
        self.assertEqual(str(seen[0].url), NASA["image_url"])
        self.assertIn("ReelsKnowledge", seen[0].headers["User-Agent"])

    def test_retries_a_server_error_then_saves_the_image(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                return httpx.Response(502, text="down")
            return httpx.Response(200, content=_jpeg(), headers={"content-type": "image/jpeg"})

        slept: list[float] = []
        with _client(handler) as client, patch(
            "edu.sources.http.time.sleep",
            side_effect=lambda delay: slept.append(delay),
        ):
            name = stage_image(NASA, self.directory, client)
        self.assertEqual(name, "ken-burns.jpg")
        self.assertEqual(calls["n"], 2)
        self.assertEqual(slept, [0.25])

    def test_does_not_retry_a_client_error(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404, text="missing")

        with _client(handler) as client:
            name = stage_image(NASA, self.directory, client)
        self.assertIsNone(name)
        self.assertEqual(calls["n"], 1)
        self.assertFalse((self.directory / "ken-burns.jpg").exists())

    def test_skips_a_response_that_is_not_an_image(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                content=b"<html></html>",
                headers={"content-type": "text/html"},
            )

        with _client(handler) as client:
            name = stage_image(NASA, self.directory, client)
        self.assertIsNone(name)
        self.assertFalse((self.directory / "ken-burns.jpg").exists())

    def test_blocked_license_does_not_call_http(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError(request.url)

        blocked = dict(NASA, license="Creative Commons Attribution 4.0")
        with _client(handler) as client:
            self.assertIsNone(stage_image(blocked, self.directory, client))
