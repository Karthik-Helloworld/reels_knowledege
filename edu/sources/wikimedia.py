"""Wikimedia Commons file search. License and author come from extmetadata."""

from __future__ import annotations

import re
from datetime import datetime

import httpx

from edu.sources.http import read_json, request_with_retry
from edu.sources.model import SourceDocument
from edu.sources.text import html_to_text

COMMONS_API = "https://commons.wikimedia.org/w/api.php"


def fetch_wikimedia(
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
            COMMONS_API,
            params={
                "action": "query",
                "generator": "search",
                "gsrsearch": query,
                "gsrnamespace": "6",
                "gsrlimit": "5",
                "prop": "imageinfo",
                "iiprop": "url|extmetadata|user",
                "format": "json",
            },
        )
    )
    pages = (payload.get("query") or {}).get("pages") or {}
    if not isinstance(pages, dict):
        return []
    for page in pages.values():
        if not isinstance(page, dict):
            continue
        document = _page_document(page, retrieved_at)
        if document is not None:
            return [document]
    return []


def _meta_value(meta: dict, key: str) -> str:
    raw = meta.get(key)
    if isinstance(raw, dict):
        return str(raw.get("value") or "").strip()
    if raw is None:
        return ""
    return str(raw).strip()


def _file_title(title: str) -> str:
    text = title[5:] if title.startswith("File:") else title
    return text.replace("_", " ").strip()


def license_reusable(name: str) -> bool:
    """True for CC0, CC BY, CC BY-SA, and public domain. NC/ND and reserved rights are not."""
    folded = " ".join(name.casefold().replace("_", " ").split())
    if any(
        token in folded
        for token in (
            "noncommercial",
            "non-commercial",
            "no derivatives",
            "all rights reserved",
            "fair use",
        )
    ):
        return False
    if re.search(r"\b(?:nc|nd)\b", folded):
        return False
    if folded in {"cc by", "cc0", "pd", "public domain"}:
        return True
    return any(
        folded == prefix or folded.startswith(prefix)
        for prefix in (
            "public domain",
            "cc0",
            "cc zero",
            "creative commons zero",
            "cc by-sa",
            "cc by ",
        )
    )


def _page_document(page: dict, retrieved_at: datetime) -> SourceDocument | None:
    infos = page.get("imageinfo") or []
    if not infos or not isinstance(infos[0], dict):
        return None
    info = infos[0]
    meta = info.get("extmetadata") if isinstance(info.get("extmetadata"), dict) else {}
    title = html_to_text(_meta_value(meta, "ObjectName")) or _file_title(
        str(page.get("title") or "")
    )
    text = html_to_text(_meta_value(meta, "ImageDescription"))
    license_name = html_to_text(_meta_value(meta, "LicenseShortName"))
    author = html_to_text(_meta_value(meta, "Artist")) or str(info.get("user") or "").strip()
    url = str(info.get("descriptionurl") or "").strip()
    if url.startswith("//"):
        url = "https:" + url
    if not title or not text or not license_name or not author or not url:
        return None
    if not license_reusable(license_name):
        return None
    return SourceDocument(
        title=title,
        text=text,
        url=url,
        license=license_name,
        author=author,
        retrieved_at=retrieved_at,
    )
