"""OpenStax textbook records from the public CMS API.

Search matches book titles. A niche can pass a fallback query, such as
``astronomy`` for science and space, when the topic itself matches nothing.
The text is the book description the API returns.
"""

from __future__ import annotations

from datetime import datetime

import httpx

from edu.sources.http import read_json, request_with_retry
from edu.sources.model import SourceDocument
from edu.sources.text import html_to_text

OPENSTAX_PAGES = "https://openstax.org/apps/cms/api/v2/pages/"
OPENSTAX_FIELDS = "title,description,authors,license_name,license_version,license_url"


def fetch_openstax(
    topic: str,
    client: httpx.Client,
    retrieved_at: datetime,
    *,
    fallback_query: str | None = None,
) -> list[SourceDocument]:
    query = topic.strip()
    documents = _search_books(query, client, retrieved_at)
    fallback = (fallback_query or "").strip()
    if documents or not fallback or fallback.casefold() == query.casefold():
        return documents
    return _search_books(fallback, client, retrieved_at)


def _search_books(
    query: str,
    client: httpx.Client,
    retrieved_at: datetime,
) -> list[SourceDocument]:
    if not query:
        return []
    payload = read_json(
        request_with_retry(
            client,
            "GET",
            OPENSTAX_PAGES,
            params={
                "type": "books.Book",
                "search": query,
                "limit": "5",
                "fields": OPENSTAX_FIELDS,
            },
        )
    )
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        document = _book_document(item, retrieved_at)
        if document is not None:
            return [document]
    return []


def _author_names(raw: object) -> str:
    names: list[str] = []
    if not isinstance(raw, list):
        return ""
    for entry in raw:
        if isinstance(entry, str) and entry.strip():
            names.append(entry.strip())
            continue
        if not isinstance(entry, dict):
            continue
        value = entry.get("value")
        record = value if isinstance(value, dict) else entry
        name = str(record.get("name") or "").strip()
        if name:
            names.append(name)
    return ", ".join(names)


def _book_document(item: dict, retrieved_at: datetime) -> SourceDocument | None:
    title = str(item.get("title") or "").strip()
    text = html_to_text(str(item.get("description") or ""))
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    url = str(meta.get("html_url") or "").strip()
    license_name = str(item.get("license_name") or "").strip()
    license_version = str(item.get("license_version") or "").strip()
    license_text = " ".join(part for part in (license_name, license_version) if part)
    author = _author_names(item.get("authors")) or "OpenStax"
    if not title or not text or not url or not license_text:
        return None
    return SourceDocument(
        title=title,
        text=text,
        url=url,
        license=license_text,
        author=author,
        retrieved_at=retrieved_at,
    )
