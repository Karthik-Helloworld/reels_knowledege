"""NASA Image and Video Library search. No API key."""

from __future__ import annotations

import urllib.parse
from datetime import datetime

import httpx

from edu.sources.http import read_json
from edu.sources.model import SourceDocument

NASA_SEARCH = "https://images-api.nasa.gov/search"
NASA_LICENSE = (
    "NASA Image and Video Library (public domain unless the record states otherwise)"
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
        client.get(
            NASA_SEARCH,
            params={"q": query, "media_type": "image", "page_size": "5"},
        )
    )
    items = ((payload.get("collection") or {}).get("items")) or []
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
                license=NASA_LICENSE,
                author=author,
                retrieved_at=retrieved_at,
            )
        ]
    return []
