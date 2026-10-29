import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import httpx

from edu.sources import fetch_sources
from edu.sources.http import client_headers, make_client, request_with_retry
from edu.sources.model import SourceDocument
from edu.sources.nasa import fetch_nasa
from edu.sources.openstax import fetch_openstax
from edu.sources.text import html_to_text
from edu.sources.wikimedia import fetch_wikimedia
from edu.sources.wikipedia import fetch_wikipedia
from edu.tests.support import load_fixture, source_handler

RETRIEVED = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


def mock_client(handler=source_handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler), headers=client_headers())


class SourceTests(unittest.TestCase):
    def test_client_identifies_itself(self) -> None:
        client = make_client()
        self.addCleanup(client.close)
        agent = client.headers["User-Agent"]
        self.assertIn("ReelsKnowledge", agent)
        self.assertIn("reels_knowledege", agent)

    def test_html_to_text_strips_tags(self) -> None:
        text = html_to_text('<span class="int-own-work">Own work</span>')
        self.assertEqual(text, "Own work")

    def test_document_rejects_a_blank_title(self) -> None:
        with self.assertRaises(ValueError):
            SourceDocument(
                title="  ",
                text="A fact.",
                url="https://example.com/a",
                license="CC BY-SA 4.0",
                author="Ada",
                retrieved_at=RETRIEVED,
            )

    def test_wikipedia_summary_from_search(self) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return source_handler(request)

        with mock_client(handler) as client:
            documents = fetch_wikipedia("Why the Moon has phases", client, RETRIEVED)
        self.assertEqual(len(documents), 1)
        document = documents[0]
        self.assertEqual(document.title, "Moon")
        self.assertEqual(document.text, "The Moon is Earth's only natural satellite.")
        self.assertEqual(document.url, "https://en.wikipedia.org/wiki/Moon")
        self.assertEqual(document.license, "CC BY-SA 4.0")
        self.assertEqual(document.author, "Wikipedia contributors")
        self.assertEqual(document.retrieved_at, RETRIEVED)
        self.assertEqual(seen[0].url.params.get("srsearch"), "Why the Moon has phases")
        self.assertTrue(seen[1].url.path.endswith("/Moon"))
        self.assertIn("ReelsKnowledge", seen[0].headers["User-Agent"])

    def test_wikipedia_miss_returns_nothing(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"query": {"search": []}})

        with mock_client(handler) as client:
            self.assertEqual(fetch_wikipedia("zzzz-no-page", client, RETRIEVED), [])

    def test_nasa_skips_blank_description(self) -> None:
        with mock_client() as client:
            documents = fetch_nasa("Moon", client, RETRIEVED)
        self.assertEqual(len(documents), 1)
        document = documents[0]
        self.assertEqual(document.title, "Nearside of the Moon")
        self.assertEqual(document.url, "https://images.nasa.gov/details/PIA12235")
        self.assertEqual(document.author, "NASA/JPL")
        self.assertEqual(document.license, "Public domain")
        self.assertIn("public domain", document.license.casefold())
        self.assertEqual(
            document.image_url,
            "https://images-assets.nasa.gov/image/PIA12235/PIA12235~thumb.jpg",
        )

    def test_openstax_parses_book_html(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.params.get("type"), "books.Book")
            return httpx.Response(200, json=load_fixture("openstax_astronomy.json"))

        with mock_client(handler) as client:
            documents = fetch_openstax("astronomy", client, RETRIEVED)
        document = documents[0]
        self.assertEqual(document.title, "Astronomy 2e")
        self.assertEqual(
            document.text,
            "Designed to meet the scope and sequence of your course, "
            "Astronomy 2e explains the sky.",
        )
        self.assertEqual(document.author, "Andrew Fraknoi")
        self.assertEqual(
            document.license,
            "Creative Commons Attribution-NonCommercial-ShareAlike License 4.0",
        )
        self.assertEqual(document.url, "https://openstax.org/details/books/astronomy-2e")

    def test_openstax_falls_back_when_the_topic_misses(self) -> None:
        searches: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            search = request.url.params.get("search", "")
            searches.append(search)
            if search.casefold() == "astronomy":
                return httpx.Response(200, json=load_fixture("openstax_astronomy.json"))
            return httpx.Response(200, json=load_fixture("openstax_empty.json"))

        with mock_client(handler) as client:
            documents = fetch_openstax(
                "Why the Moon has phases",
                client,
                RETRIEVED,
                fallback_query="astronomy",
            )
        self.assertEqual(searches, ["Why the Moon has phases", "astronomy"])
        self.assertEqual(documents[0].title, "Astronomy 2e")

    def test_openstax_does_not_repeat_the_same_query(self) -> None:
        searches: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            searches.append(request.url.params.get("search", ""))
            return httpx.Response(200, json=load_fixture("openstax_empty.json"))

        with mock_client(handler) as client:
            documents = fetch_openstax(
                "Astronomy",
                client,
                RETRIEVED,
                fallback_query="astronomy",
            )
        self.assertEqual(documents, [])
        self.assertEqual(searches, ["Astronomy"])

    def test_wikimedia_skips_a_file_without_a_license(self) -> None:
        with mock_client() as client:
            documents = fetch_wikimedia("Moon", client, RETRIEVED)
        document = documents[0]
        self.assertEqual(document.title, "Full Moon")
        self.assertEqual(document.text, "The Moon after sunset.")
        self.assertEqual(document.author, "Ada")
        self.assertEqual(document.license, "CC BY-SA 4.0")
        self.assertEqual(
            document.url,
            "https://commons.wikimedia.org/wiki/File:Full_Moon.jpg",
        )
        self.assertEqual(
            document.image_url,
            "https://upload.wikimedia.org/wikipedia/commons/full-moon.jpg",
        )

    def test_fetch_sources_keeps_niche_order(self) -> None:
        with mock_client() as client:
            fetched = fetch_sources(
                "Why the Moon has phases",
                ("wikipedia", "nasa", "openstax", "wikimedia"),
                client,
                RETRIEVED,
                openstax_query="astronomy",
            )
        self.assertEqual(fetched.failures, [])
        documents = fetched.documents
        self.assertEqual(
            [document.title for document in documents],
            ["Moon", "Nearside of the Moon", "Astronomy 2e", "Full Moon"],
        )
        for document in documents:
            self.assertTrue(document.title)
            self.assertTrue(document.text)
            self.assertTrue(document.url)
            self.assertTrue(document.license)
            self.assertTrue(document.author)
            self.assertEqual(document.retrieved_at, RETRIEVED)

    def test_nasa_skips_terms_that_are_not_public_domain(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "images-assets.nasa.gov":
                nasa_id = request.url.path.split("/")[2]
                terms = "Public domain" if nasa_id == "PD1" else "Creative Commons Attribution 4.0"
                return httpx.Response(200, json={"XMP:UsageTerms": terms})
            if request.url.path.startswith("/metadata/"):
                nasa_id = request.url.path.rsplit("/", 1)[-1]
                return httpx.Response(
                    200,
                    json={"location": f"https://images-assets.nasa.gov/image/{nasa_id}/metadata.json"},
                )
            return httpx.Response(
                200,
                json={
                    "collection": {
                        "items": [
                            {
                                "data": [
                                    {
                                        "title": "Restricted",
                                        "description": "A copyrighted frame.",
                                        "nasa_id": "CCBY1",
                                    }
                                ]
                            },
                            {
                                "data": [
                                    {
                                        "title": "Free frame",
                                        "description": "A public-domain frame.",
                                        "nasa_id": "PD1",
                                        "secondary_creator": "NASA",
                                    }
                                ]
                            },
                        ]
                    }
                },
            )

        with mock_client(handler) as client:
            documents = fetch_nasa("Moon", client, RETRIEVED)
        self.assertEqual([document.title for document in documents], ["Free frame"])
        self.assertEqual(documents[0].license, "Public domain")

    def test_retries_a_server_error_then_succeeds(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] < 3:
                return httpx.Response(502, json={"error": "down"})
            return httpx.Response(200, json={"ok": True})

        slept: list[float] = []
        with mock_client(handler) as client, patch(
            "edu.sources.http.time.sleep",
            side_effect=lambda delay: slept.append(delay),
        ):
            response = request_with_retry(client, "GET", "https://example.com/item")
        self.assertEqual(response.json()["ok"], True)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(slept, [0.25, 0.5])

    def test_does_not_retry_a_client_error(self) -> None:
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(404, json={"error": "missing"})

        with mock_client(handler) as client:
            response = request_with_retry(client, "GET", "https://example.com/missing")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(calls["n"], 1)

    def test_one_down_source_does_not_drop_the_others(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "en.wikipedia.org":
                return httpx.Response(503, json={"error": "down"})
            return source_handler(request)

        with mock_client(handler) as client, patch("edu.sources.http.time.sleep"):
            fetched = fetch_sources(
                "Why the Moon has phases",
                ("wikipedia", "nasa", "openstax", "wikimedia"),
                client,
                RETRIEVED,
                openstax_query="astronomy",
            )
        self.assertEqual(
            [document.title for document in fetched.documents],
            ["Nearside of the Moon", "Astronomy 2e", "Full Moon"],
        )
        self.assertEqual(len(fetched.failures), 1)
        self.assertEqual(fetched.failures[0].source, "wikipedia")
        self.assertIn("503", fetched.failures[0].message)


if __name__ == "__main__":
    unittest.main()
