"""Shared helpers for the Phase 0 video core.

Video constants match core/src/theme.ts.
"""

from __future__ import annotations

import shutil
from pathlib import Path

# Keep in sync with core/src/theme.ts `theme.video`.
VIDEO_WIDTH = 1080
VIDEO_HEIGHT = 1920
VIDEO_FPS = 30


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def which(name: str) -> str | None:
    return shutil.which(name)
