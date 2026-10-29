"""Build one reel package, fact-check it, then render it.

`make edu-video TOPIC="..."` calls this module. A failed fact report blocks
the video: the script package is not written, and the process exits non-zero.
A passing report writes the package, marks the topic used, and renders an mp4.
If fetch or Ollama is down, a checked-in fixture is fact-checked and rendered
instead. That fallback does not mark a topic used.
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
from edu.scripts.fact_check import (
    FactCheckError,
    default_report_path,
    fact_check,
    format_block_message,
    load_llm_fixture,
    package_parts,
    write_report,
)
from edu.scripts.render_reel import render_reel
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


def complete_video(
    topic: str | None,
    *,
    source_client: httpx.Client,
    ollama_client: httpx.Client,
    niche: NicheConfig | None = None,
    topics_path: Path | None = None,
    retrieved_at: datetime | None = None,
    rng: random.Random | None = None,
    out_path: Path | None = None,
    report_path: Path | None = None,
    fact_llm: dict | None = None,
) -> Path:
    """Generate a script, write fact_report.json, and keep the package only if it passes."""
    config = niche or load_niche()
    package = build_package(
        topic,
        source_client=source_client,
        ollama_client=ollama_client,
        niche=config,
        topics_path=topics_path,
        retrieved_at=retrieved_at,
        rng=rng,
    )
    script, documents = package_parts(package)
    report = fact_check(
        script,
        documents,
        niche=config,
        client=ollama_client,
        llm_payload=fact_llm,
    )
    destination = out_path or default_output_path(str(package["topic"]))
    report_destination = report_path or default_report_path(destination)
    write_report(report, report_destination)
    if not report.passed:
        raise FactCheckError(
            format_block_message(report) + f"\nReport: {report_destination}"
        )
    package["fact_check"] = {"passed": True, "report": report_destination.name}
    return save_successful_package(package, destination, topics_path)


def fixture_package_path() -> Path:
    return repo_root() / "edu" / "tests" / "fixtures" / "render_package.json"


def fixture_verdict_path() -> Path:
    return repo_root() / "edu" / "tests" / "fixtures" / "render_llm_pass.json"


def write_fixture_package(
    *,
    out_path: Path | None = None,
    report_path: Path | None = None,
) -> Path:
    """Fact-check the dry-run package and write it without marking a topic used."""

    package = json.loads(fixture_package_path().read_text(encoding="utf-8"))
    config = load_niche()
    script, documents = package_parts(package)
    report = fact_check(
        script,
        documents,
        niche=config,
        llm_payload=load_llm_fixture(fixture_verdict_path()),
    )
    destination = out_path or default_output_path(str(package["topic"]))
    report_destination = report_path or default_report_path(destination)
    write_report(report, report_destination)
    if not report.passed:
        raise FactCheckError(
            format_block_message(report) + f"\nReport: {report_destination}"
        )
    package["fact_check"] = {
        "passed": True,
        "report": report_destination.name,
        "fixture": True,
    }
    return write_package(package, destination)


def write_video_package(
    topic: str | None,
    *,
    out_path: Path | None = None,
    fact_llm: dict | None = None,
    topics_path: Path | None = None,
) -> Path:
    with make_client() as source_client, make_client() as ollama_client:
        return complete_video(
            topic,
            source_client=source_client,
            ollama_client=ollama_client,
            topics_path=topics_path,
            out_path=out_path,
            fact_llm=fact_llm,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fetch sources, write a grounded script, and fact-check it."
    )
    parser.add_argument("--topic", default="")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument("--niche", type=Path, default=None)
    parser.add_argument("--topics", type=Path, default=None)
    parser.add_argument(
        "--fact-fixture",
        type=Path,
        default=None,
        help="Canned fact-check verdict. Skips the second Ollama call.",
    )
    parser.add_argument(
        "--no-render",
        action="store_true",
        help="Write the package and fact report, and skip the Remotion render.",
    )
    args = parser.parse_args(argv)
    if args.fact_fixture is not None and not args.fact_fixture.is_file():
        print(
            f"edu-video: fact-check fixture not found: {args.fact_fixture}",
            file=sys.stderr,
        )
        return 1
    fact_llm = load_llm_fixture(args.fact_fixture) if args.fact_fixture else None
    try:
        config = load_niche(args.niche or niche_path())
        with make_client() as source_client, make_client() as ollama_client:
            path = complete_video(
                args.topic,
                source_client=source_client,
                ollama_client=ollama_client,
                niche=config,
                topics_path=args.topics or default_topics_path(),
                out_path=args.out,
                report_path=args.report,
                fact_llm=fact_llm,
            )
    except FactCheckError as exc:
        print(f"edu-video: {exc}", file=sys.stderr)
        return 1
    except (httpx.ConnectError, GroundingError, SourceError, httpx.HTTPError) as exc:
        print(
            "edu-video: live fetch or Ollama is unavailable "
            f"({exc}). Fact-checking the fixture package and rendering that. "
            "Piper and faster-whisper are optional.",
            file=sys.stderr,
        )
        try:
            path = write_fixture_package(out_path=args.out, report_path=args.report)
        except FactCheckError as fixture_exc:
            print(f"edu-video: {fixture_exc}", file=sys.stderr)
            return 1
    except (ValueError, OSError) as exc:
        print(f"edu-video: {exc}", file=sys.stderr)
        return 1
    print(path)
    print(args.report or default_report_path(path))
    if args.no_render:
        return 0
    try:
        video, caption = render_reel(path)
    except (RuntimeError, OSError) as exc:
        print(f"edu-video: {exc}", file=sys.stderr)
        return 1
    print(video)
    print(caption)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
