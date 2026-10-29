"""Fetch grounded source documents for one topic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from edu.sources.http import SourceError
from edu.sources.model import SourceDocument
from edu.sources.nasa import fetch_nasa
from edu.sources.openstax import fetch_openstax
from edu.sources.wikimedia import fetch_wikimedia
from edu.sources.wikipedia import fetch_wikipedia

ADAPTERS = ("wikipedia", "nasa", "openstax", "wikimedia")


@dataclass(frozen=True)
class SourceFailure:
    source: str
    message: str


@dataclass(frozen=True)
class FetchResult:
    documents: list[SourceDocument]
    failures: list[SourceFailure]


def fetch_sources(
    topic: str,
    source_names: list[str] | tuple[str, ...],
    client: httpx.Client,
    retrieved_at: datetime,
    *,
    openstax_query: str | None = None,
) -> FetchResult:
    documents: list[SourceDocument] = []
    failures: list[SourceFailure] = []
    for name in source_names:
        try:
            if name == "wikipedia":
                found = fetch_wikipedia(topic, client, retrieved_at)
            elif name == "nasa":
                found = fetch_nasa(topic, client, retrieved_at)
            elif name == "openstax":
                found = fetch_openstax(
                    topic,
                    client,
                    retrieved_at,
                    fallback_query=openstax_query,
                )
            elif name == "wikimedia":
                found = fetch_wikimedia(topic, client, retrieved_at)
            else:
                raise ValueError(f"unknown source: {name}")
        except SourceError as exc:
            failures.append(SourceFailure(name, str(exc)))
            continue
        documents.extend(found)
    return FetchResult(documents, failures)
