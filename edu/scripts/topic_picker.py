"""Choose a reel topic from edu/topics.csv, or keep an explicit one."""

from __future__ import annotations

import argparse
import csv
import random
from dataclasses import dataclass
from pathlib import Path

from core.utils import repo_root


@dataclass(frozen=True)
class TopicChoice:
    topic: str
    angle: str


def default_topics_path() -> Path:
    return repo_root() / "edu" / "topics.csv"


def load_topics(path: Path | None = None) -> list[TopicChoice]:
    csv_path = path or default_topics_path()
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    choices: list[TopicChoice] = []
    for row in rows:
        topic = str(row.get("topic") or "").strip()
        if not topic:
            continue
        angle = str(row.get("angle") or "").strip()
        choices.append(TopicChoice(topic, angle))
    if not choices:
        raise ValueError(f"no topics in {csv_path}")
    return choices


def pick_topic(
    explicit: str | None,
    path: Path | None = None,
    rng: random.Random | None = None,
) -> TopicChoice:
    choices = load_topics(path)
    text = (explicit or "").strip()
    if text:
        for choice in choices:
            if choice.topic.casefold() == text.casefold():
                return choice
        return TopicChoice(text, "")
    picker = rng or random.Random()
    return picker.choice(choices)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pick a reel topic.")
    parser.add_argument("--topic", default="")
    parser.add_argument("--topics", type=Path, default=None)
    args = parser.parse_args(argv)
    choice = pick_topic(args.topic, args.topics)
    print(choice.topic)
    if choice.angle:
        print(choice.angle)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
