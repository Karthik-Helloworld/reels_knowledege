"""Fetch grounded source documents for one topic."""

from __future__ import annotations

from datetime import datetime

import httpx

from edu.sources.model import SourceDocument
from edu.sources.nasa import fetch_nasa
from edu.sources.openstax import fetch_openstax
from edu.sources.wikipedia import fetch_wikipedia
from edu.sources.wikimedia import fetch_wikimedia

ADAPTERS = ("wikipedia", "nasa", "openstax", "wikimedia")


def fetch_sources(
    topic: str,
    source_names: list[str] | tuple[str, ...],
    client: httpx.Client,
    retrieved_at: datetime,
    *,
    openstax_query: str | None = None,
) -> list[SourceDocument]:
    documents: list[SourceDocument] = []
    for name in source_names:
        if name == "wikipedia":
            documents.extend(fetch_wikipedia(topic, client, retrieved_at))
        elif name == "nasa":
            documents.extend(fetch_nasa(topic, client, retrieved_at))
        elif name == "openstax":
            documents.extend(
                fetch_openstax(
                    topic,
                    client,
                    retrieved_at,
                    fallback_query=openstax_query,
                )
            )
        elif name == "wikimedia":
            documents.extend(fetch_wikimedia(topic, client, retrieved_at))
        else:
            raise ValueError(f"unknown source: {name}")
    return documents
