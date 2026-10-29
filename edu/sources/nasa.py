"""NASA Image and Video Library search. No API key.

Rights come from each asset's metadata (`XMP:UsageTerms` and related fields).
Items are kept only when those terms are public domain or CC0.
"""

from __future__ import annotations

import urllib.parse
from datetime import datetime

import httpx

from edu.sources.http import SourceError, read_json, request_with_retry
from edu.sources.model import SourceDocument

NASA_SEARCH = "https://images-api.nasa.gov/search"
NASA_METADATA = "https://images-api.nasa.gov/metadata/"
_RIGHTS_KEYS = (
    "XMP:UsageTerms",
    "XMP:Rights",
    "IPTC:CopyrightNotice",
    "EXIF:Copyright",
)


def fetch_nasa(
    topic: str,
    client: httpx.Client,
    retrieved_at: datetime,
) -> list[SourceDocument]:
    query = topic.strip()
    if not query:
        return []
    payload = read_json(
        request_with_retry(
            client,
            "GET",
            NASA_SEARCH,
            params={"q": query, "media_type": "image", "page_size": "5"},
        )
    )
    items = ((payload.get("collection") or {}).get("items")) or []
    metadata_errors: list[SourceError] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        data_list = item.get("data") or []
        if not data_list or not isinstance(data_list[0], dict):
            continue
        data = data_list[0]
        nasa_id = str(data.get("nasa_id") or "").strip()
        title = str(data.get("title") or "").strip()
        text = str(data.get("description") or data.get("description_508") or "").strip()
        if not nasa_id or not title or not text:
            continue
        try:
            terms = _usage_terms(client, nasa_id)
        except SourceError as exc:
            metadata_errors.append(exc)
            continue
        if not public_domain_or_cc0(terms):
            continue
        author = str(
            data.get("secondary_creator")
            or data.get("photographer")
            or data.get("center")
            or "NASA"
        ).strip() or "NASA"
        quoted_id = urllib.parse.quote(nasa_id, safe="")
        return [
            SourceDocument(
                title=title,
                text=text,
                url=f"https://images.nasa.gov/details/{quoted_id}",
                license=terms,
                author=author,
                retrieved_at=retrieved_at,
                image_url=_image_href(item),
            )
        ]
    if metadata_errors:
        raise metadata_errors[-1]
    return []


def _usage_terms(client: httpx.Client, nasa_id: str) -> str:
    quoted = urllib.parse.quote(nasa_id, safe="")
    pointer = read_json(request_with_retry(client, "GET", NASA_METADATA + quoted))
    location = str(pointer.get("location") or "").strip()
    if not location:
        return ""
    meta = read_json(request_with_retry(client, "GET", location))
    chunks: list[str] = []
    for key in _RIGHTS_KEYS:
        value = str(meta.get(key) or "").strip()
        if value and value not in chunks:
            chunks.append(value)
    return "\n".join(chunks)


def _image_href(item: dict) -> str | None:
    for link in item.get("links") or []:
        if not isinstance(link, dict):
            continue
        href = str(link.get("href") or "").strip()
        if not href.startswith(("http://", "https://")):
            continue
        path = href.lower().split("?", 1)[0]
        if any(path.endswith(ext) for ext in (".jpg", ".jpeg", ".png", ".webp")):
            return href
    return None


def public_domain_or_cc0(terms: str) -> bool:
    folded = terms.casefold()
    if not folded:
        return False
    if any(
        phrase in folded
        for phrase in (
            "all rights reserved",
            "non-commercial",
            "noncommercial",
            "no derivatives",
        )
    ):
        return False
    return any(
        phrase in folded
        for phrase in ("public domain", "cc0", "creative commons zero", "cc zero")
    )
