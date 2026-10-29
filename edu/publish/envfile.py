"""Read a local env file without overriding variables already set in the process."""

from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path) -> list[str]:
    """Set missing keys from ``path``. Returns the keys that were filled in."""

    if not path.is_file():
        return []
    filled: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = value.strip().strip('"').strip("'")
        filled.append(key)
    return filled


def present(*names: str) -> list[str]:
    """Names that are unset or blank."""

    return [name for name in names if not os.environ.get(name, "").strip()]
