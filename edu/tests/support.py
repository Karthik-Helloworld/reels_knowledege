"""Shared fixture loading and a mock HTTP transport for source tests."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def source_handler(request: httpx.Request) -> httpx.Response:
    host = request.url.host
    if host == "en.wikipedia.org":
        if request.url.path.startswith("/api/rest_v1/page/summary/"):
            return httpx.Response(200, json=load_fixture("wikipedia_summary.json"))
        return httpx.Response(200, json=load_fixture("wikipedia_search.json"))
    if host == "images-api.nasa.gov":
        return httpx.Response(200, json=load_fixture("nasa_search.json"))
    if host == "openstax.org":
        search = request.url.params.get("search", "")
        if search.casefold() == "astronomy":
            return httpx.Response(200, json=load_fixture("openstax_astronomy.json"))
        return httpx.Response(200, json=load_fixture("openstax_empty.json"))
    if host == "commons.wikimedia.org":
        return httpx.Response(200, json=load_fixture("wikimedia_search.json"))
    return httpx.Response(404, json={"error": f"unexpected host {host}"})
