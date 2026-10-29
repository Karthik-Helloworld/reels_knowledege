"""Build one reel package: topic, sources, and a grounded script.

`make edu-video TOPIC="..."` calls this module. The package is JSON on disk.
Phase 1 does not render, fact-check, or publish.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx

from core.utils import ensure_parent, repo_root
from edu.niche import NicheConfig, load_niche, niche_path
from edu.scripts.script_gen import GroundingError, generate_script
from edu.scripts.topic_picker import (
    TopicChoice,
    default_topics_path,
    mark_topic_used,
    pick_topic,
)
from edu.sources import fetch_sources
from edu.sources.http import SourceError, make_client


def topic_slug(topic: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", topic.casefold()).strip("-")
    return (slug[:80] or "topic")


def default_output_path(topic: str) -> Path:
    return repo_root() / "edu" / "out" / f"{topic_slug(topic)}.json"


def build_package(
    topic: str | None,
    *,
    source_client: httpx.Client,
    ollama_client: httpx.Client,
    niche: NicheConfig | None = None,
    topics_path: Path | None = None,
    retrieved_at: datetime | None = None,
    rng: random.Random | None = None,
) -> dict:
    config = niche or load_niche()
    choice: TopicChoice = pick_topic(topic, topics_path, rng=rng)
    stamp = retrieved_at or datetime.now(timezone.utc)
    fetched = fetch_sources(
        choice.topic,
        config.sources,
        source_client,
        stamp,
        openstax_query=config.openstax_query,
    )
    for failure in fetched.failures:
        print(
            f"edu-video: {failure.source} is down: {failure.message}",
            file=sys.stderr,
        )
    documents = fetched.documents
    if not documents:
        detail = "; ".join(f"{item.source}: {item.message}" for item in fetched.failures)
        message = f"no sources returned for {choice.topic}"
        if detail:
            message = f"{message}. {detail}"
        raise GroundingError(message)
    script = generate_script(
        topic=choice.topic,
        angle=choice.angle,
        niche=config,
        documents=documents,
        client=ollama_client,
    )
    return {
        "niche": config.name,
        "topic": choice.topic,
        "angle": choice.angle,
        "sources": [document.model_dump(mode="json") for document in documents],
        "source_failures": [
            {"source": failure.source, "message": failure.message}
            for failure in fetched.failures
        ],
        "script": script.model_dump(mode="json"),
    }


def write_package(package: dict, path: Path) -> Path:
    ensure_parent(path)
    path.write_text(json.dumps(package, indent=2) + "\n", encoding="utf-8")
    return path


def save_successful_package(
    package: dict,
    destination: Path,
    topics_path: Path | None = None,
) -> Path:
    path = write_package(package, destination)
    mark_topic_used(str(package["topic"]), topics_path)
    return path


def write_video_package(topic: str | None, *, out_path: Path | None = None) -> Path:
    with make_client() as source_client, make_client() as ollama_client:
        package = build_package(
            topic,
            source_client=source_client,
            ollama_client=ollama_client,
        )
    destination = out_path or default_output_path(str(package["topic"]))
    return save_successful_package(package, destination)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch sources and write a grounded reel script."
    )
    parser.add_argument("--topic", default="")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--niche", type=Path, default=None)
    parser.add_argument("--topics", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        if args.niche or args.topics:
            config = load_niche(args.niche or niche_path())
            with make_client() as source_client, make_client() as ollama_client:
                package = build_package(
                    args.topic,
                    source_client=source_client,
                    ollama_client=ollama_client,
                    niche=config,
                    topics_path=args.topics or default_topics_path(),
                )
            destination = args.out or default_output_path(str(package["topic"]))
            path = save_successful_package(
                package,
                destination,
                args.topics or default_topics_path(),
            )
        else:
            path = write_video_package(args.topic, out_path=args.out)
    except httpx.ConnectError as exc:
        target = str(exc.request.url) if exc.request is not None else ""
        if "/api/chat" in target or ":11434" in target:
            print(
                "edu-video: Ollama is not reachable. Start it locally and pull "
                "the model named in edu/config/niche.yaml.",
                file=sys.stderr,
            )
        else:
            print(f"edu-video: could not reach a source: {exc}", file=sys.stderr)
        return 1
    except (GroundingError, SourceError, httpx.HTTPError, ValueError, OSError) as exc:
        print(f"edu-video: {exc}", file=sys.stderr)
        return 1
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
