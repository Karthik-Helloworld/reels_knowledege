"""Pending render bundles waiting for a human decision.

Each item is a directory under ``edu/out/pending/<id>/`` with the video, the
script, the claims, the fact report, and the caption. Nothing in that
directory is publishable.
"""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

from core.utils import repo_root

_ITEM_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
_FILES = (
    "video.mp4",
    "script.json",
    "claims.json",
    "fact_report.json",
    "caption.txt",
    "sources.json",
)


def edu_out() -> Path:
    return repo_root() / "edu" / "out"


def pending_root() -> Path:
    raw = os.environ.get("EDU_PENDING_DIR", "").strip()
    return Path(raw) if raw else edu_out() / "pending"


def approved_root() -> Path:
    raw = os.environ.get("EDU_APPROVED_DIR", "").strip()
    return Path(raw) if raw else edu_out() / "approved"


def rejected_root() -> Path:
    raw = os.environ.get("EDU_REJECTED_DIR", "").strip()
    return Path(raw) if raw else edu_out() / "rejected"


def check_item_id(item_id: str) -> str:
    if not _ITEM_ID.fullmatch(item_id):
        raise ValueError(f"invalid review id: {item_id}")
    return item_id


def list_pending(root: Path | None = None) -> list[Path]:
    directory = root or pending_root()
    if not directory.is_dir():
        return []
    found = [
        path
        for path in directory.iterdir()
        if path.is_dir() and _ITEM_ID.fullmatch(path.name) and _complete(path)
    ]
    return sorted(found, key=lambda path: path.name)


def _complete(directory: Path) -> bool:
    return all((directory / name).is_file() for name in _FILES)


def allocate_id(stem: str, root: Path, taken: set[str] | None = None) -> str:
    base = stem if _ITEM_ID.fullmatch(stem) else "video"
    reserved = set(taken or ())
    candidate = base
    number = 2
    while candidate in reserved or (root / candidate).exists():
        candidate = f"{base}-{number}"
        number += 1
        if number > 1000:
            raise RuntimeError(f"could not allocate a pending id for {stem}")
    return candidate


def stage_pending(
    package_path: Path,
    video_path: Path,
    caption_path: Path,
    report_path: Path,
    *,
    root: Path | None = None,
    taken: set[str] | None = None,
) -> Path:
    """Copy a finished render into the pending review directory."""

    for path in (package_path, video_path, caption_path, report_path):
        if not path.is_file():
            raise FileNotFoundError(path)
    package = json.loads(package_path.read_text(encoding="utf-8"))
    script = package.get("script")
    if not isinstance(script, dict):
        raise ValueError(f"{package_path} has no script object")
    claims = script.get("claims")
    if not isinstance(claims, list) or not claims:
        raise ValueError(f"{package_path} has no claims")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError(f"{report_path} must be a JSON object")
    sources = package.get("sources")
    if sources is None:
        sources = []
    if not isinstance(sources, list):
        raise ValueError(f"{package_path} sources must be a list")
    directory = root or pending_root()
    directory.mkdir(parents=True, exist_ok=True)
    item_id = allocate_id(package_path.stem, directory, taken)
    destination = directory / item_id
    destination.mkdir()
    shutil.copy2(video_path, destination / "video.mp4")
    (destination / "script.json").write_text(
        json.dumps(script, indent=2) + "\n",
        encoding="utf-8",
    )
    (destination / "claims.json").write_text(
        json.dumps(claims, indent=2) + "\n",
        encoding="utf-8",
    )
    (destination / "fact_report.json").write_text(
        json.dumps(report, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copy2(caption_path, destination / "caption.txt")
    (destination / "sources.json").write_text(
        json.dumps(sources, indent=2) + "\n",
        encoding="utf-8",
    )
    return destination


def format_item(directory: Path) -> str:
    script = json.loads((directory / "script.json").read_text(encoding="utf-8"))
    claims = json.loads((directory / "claims.json").read_text(encoding="utf-8"))
    report = json.loads((directory / "fact_report.json").read_text(encoding="utf-8"))
    caption = (directory / "caption.txt").read_text(encoding="utf-8").rstrip()
    lines = [
        f"id: {directory.name}",
        f"video: {directory / 'video.mp4'}",
        f"topic: {script.get('topic', '')}",
        "script:",
        f"  hook: {script.get('hook', '')}",
    ]
    for index, beat in enumerate(script.get("beats") or []):
        if isinstance(beat, dict):
            lines.append(f"  beat {index}: {beat.get('narration', '')}")
    lines.append(f"  closing: {script.get('closing', '')}")
    lines.append(f"  key_takeaway: {script.get('key_takeaway', '')}")
    lines.append(f"  cta: {script.get('cta', '')}")
    lines.append("claims:")
    if isinstance(claims, list):
        for claim in claims:
            if isinstance(claim, dict):
                lines.append(f"  - {claim.get('text', '')}")
                lines.append(f"    source: {claim.get('source_url', '')}")
    lines.append("fact_report:")
    lines.append(f"  passed: {report.get('passed')}")
    for claim in report.get("claims") or []:
        if isinstance(claim, dict):
            lines.append(
                f"  - {claim.get('id')}: passed={claim.get('passed')} "
                f"reason={claim.get('llm_reason', '')}"
            )
    lines.append("caption:")
    lines.append(caption)
    lines.append("status: pending (not approved, cannot be published)")
    return "\n".join(lines) + "\n"


def write_caption(directory: Path, caption: str) -> None:
    text = caption.strip()
    if not text:
        raise ValueError("caption must not be blank")
    (directory / "caption.txt").write_text(text + "\n", encoding="utf-8")
