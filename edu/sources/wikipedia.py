"""Wikipedia REST summary, found through a MediaWiki search."""

from __future__ import annotations

import urllib.parse
from datetime import datetime

import httpx

from edu.sources.http import read_json, request_with_retry
from edu.sources.model import SourceDocument

WIKI_API = "https://en.wikipedia.org/w/api.php"
WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/"
# English Wikipedia article text is CC BY-SA 4.0 on every page, so this is not
# a per-item lookup. NASA and Commons rights vary and are read from each record.
WIKI_LICENSE = "CC BY-SA 4.0"
WIKI_AUTHOR = "Wikipedia contributors"


def fetch_wikipedia(
    topic: str,
    client: httpx.Client,
    retrieved_at: datetime,
) -> list[SourceDocument]:
    query = topic.strip()
    if not query:
        return []
    search = read_json(
        request_with_retry(
            client,
            "GET",
            WIKI_API,
            params={
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": "1",
                "format": "json",
                "utf8": "1",
            },
        )
    )
    hits = (search.get("query") or {}).get("search") or []
    if not hits or not isinstance(hits[0], dict):
        return []
    title = str(hits[0].get("title") or "").strip()
    if not title:
        return []
    summary_url = WIKI_SUMMARY + urllib.parse.quote(title.replace(" ", "_"), safe="")
    summary_response = request_with_retry(client, "GET", summary_url)
    if summary_response.status_code == 404:
        return []
    summary = read_json(summary_response)
    extract = str(summary.get("extract") or "").strip()
    page = ((summary.get("content_urls") or {}).get("desktop") or {}).get("page")
    page_title = str(summary.get("title") or title).strip()
    if not extract or not page or not page_title:
        return []
    return [
        SourceDocument(
            title=page_title,
            text=extract,
            url=str(page),
            license=WIKI_LICENSE,
            author=WIKI_AUTHOR,
            retrieved_at=retrieved_at,
        )
    ]
