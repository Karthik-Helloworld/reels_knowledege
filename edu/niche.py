"""Load the niche file that steers topic search and script generation."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from core.utils import repo_root


@dataclass(frozen=True)
class NicheConfig:
    name: str
    audience: str
    tone: str
    length_seconds: int
    openstax_query: str
    ollama_host: str
    ollama_model: str
    sources: tuple[str, ...]


def niche_path() -> Path:
    return repo_root() / "edu" / "config" / "niche.yaml"


def load_niche(path: Path | None = None) -> NicheConfig:
    config_path = path or niche_path()
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{config_path} must be a mapping")
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("niche name is required")
    source_raw = raw.get("sources")
    if not isinstance(source_raw, list) or not source_raw:
        raise ValueError("niche sources must be a non-empty list")
    sources = tuple(str(item).strip() for item in source_raw if str(item).strip())
    if not sources:
        raise ValueError("niche sources must be a non-empty list")
    ollama = raw.get("ollama") or {}
    if not isinstance(ollama, dict):
        raise ValueError("niche ollama must be a mapping")
    host = os.environ.get("OLLAMA_HOST", "").strip() or str(
        ollama.get("host") or "http://127.0.0.1:11434"
    ).strip()
    model = os.environ.get("OLLAMA_MODEL", "").strip() or str(
        ollama.get("model") or "qwen2.5:7b"
    ).strip()
    length = raw.get("length_seconds") or 30
    return NicheConfig(
        name=name,
        audience=str(raw.get("audience") or "").strip(),
        tone=str(raw.get("tone") or "").strip(),
        length_seconds=int(length),
        openstax_query=str(raw.get("openstax_query") or "").strip(),
        ollama_host=host,
        ollama_model=model,
        sources=sources,
    )
