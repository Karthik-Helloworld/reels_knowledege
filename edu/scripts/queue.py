"""SQLite queue for videos a person has approved.

Rejected videos are never inserted. A row exists only after approval, and
``can_publish`` is false until that row is there. ``scheduled_at`` spaces
approvals at the configured cadence. The default is one video per day.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from core.utils import repo_root
from edu.niche import niche_path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS queue (
    id TEXT PRIMARY KEY,
    topic TEXT NOT NULL,
    video_path TEXT NOT NULL,
    script_path TEXT NOT NULL,
    claims_path TEXT NOT NULL,
    fact_report_path TEXT NOT NULL,
    caption_path TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status = 'approved'),
    scheduled_at TEXT NOT NULL,
    approved_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('approve', 'reject', 'edit')),
    actor TEXT NOT NULL,
    at TEXT NOT NULL,
    detail TEXT NOT NULL
);
"""


def queue_db_path() -> Path:
    raw = os.environ.get("EDU_QUEUE_DB", "").strip()
    return Path(raw) if raw else repo_root() / "edu" / "out" / "queue.sqlite"


def connect(path: Path | None = None) -> sqlite3.Connection:
    database = path or queue_db_path()
    database.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript(_SCHEMA)
    return connection


def cadence_interval(per_day: float) -> timedelta:
    if per_day <= 0:
        raise ValueError("cadence_per_day must be positive")
    return timedelta(seconds=86400 / per_day)


def configured_cadence(niche_file: Path | None = None) -> float:
    raw = os.environ.get("REVIEW_CADENCE_PER_DAY", "").strip()
    if raw:
        return float(raw)
    path = niche_file or niche_path()
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict) or loaded.get("cadence_per_day") is None:
        return 1.0
    return float(loaded["cadence_per_day"])


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    return value.astimezone(timezone.utc)


def isoformat(value: datetime) -> str:
    return _aware(value).isoformat(timespec="seconds")


def scheduled_times(connection: sqlite3.Connection) -> list[datetime]:
    rows = connection.execute("SELECT scheduled_at FROM queue").fetchall()
    found: list[datetime] = []
    for row in rows:
        parsed = datetime.fromisoformat(str(row["scheduled_at"]))
        found.append(_aware(parsed))
    return found


def next_scheduled_at(
    connection: sqlite3.Connection,
    now: datetime,
    per_day: float,
) -> datetime:
    """First approval is scheduled at ``now``. Later ones wait one cadence slot."""

    current = _aware(now)
    existing = scheduled_times(connection)
    if not existing:
        return current
    slot = max(existing) + cadence_interval(per_day)
    if slot < current:
        return current
    return slot


def enqueue(
    connection: sqlite3.Connection,
    *,
    item_id: str,
    topic: str,
    directory: Path,
    now: datetime,
    per_day: float,
) -> datetime:
    scheduled = next_scheduled_at(connection, now, per_day)
    approved_at = _aware(now)
    connection.execute(
        """
        INSERT INTO queue (
            id, topic, video_path, script_path, claims_path, fact_report_path,
            caption_path, status, scheduled_at, approved_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'approved', ?, ?)
        """,
        (
            item_id,
            topic,
            str(directory / "video.mp4"),
            str(directory / "script.json"),
            str(directory / "claims.json"),
            str(directory / "fact_report.json"),
            str(directory / "caption.txt"),
            isoformat(scheduled),
            isoformat(approved_at),
        ),
    )
    connection.commit()
    return scheduled


def get_item(connection: sqlite3.Connection, item_id: str) -> sqlite3.Row | None:
    return connection.execute("SELECT * FROM queue WHERE id = ?", (item_id,)).fetchone()


def can_publish(connection: sqlite3.Connection, item_id: str) -> bool:
    """True only after approval. Pending and rejected ids are false."""

    row = get_item(connection, item_id)
    return row is not None and row["status"] == "approved"


def record_decision(
    connection: sqlite3.Connection,
    *,
    item_id: str,
    action: str,
    at: datetime,
    detail: str,
    actor: str | None = None,
) -> None:
    """Append one review decision. Approve, reject, and edit all write a row."""

    who = (actor or os.environ.get("REVIEW_ACTOR", "")).strip() or "local"
    connection.execute(
        """
        INSERT INTO decisions (item_id, action, actor, at, detail)
        VALUES (?, ?, ?, ?, ?)
        """,
        (item_id, action, who, isoformat(at), detail),
    )
    connection.commit()


def list_decisions(connection: sqlite3.Connection, item_id: str | None = None) -> list[sqlite3.Row]:
    if item_id is None:
        return list(connection.execute("SELECT * FROM decisions ORDER BY id"))
    return list(
        connection.execute(
            "SELECT * FROM decisions WHERE item_id = ? ORDER BY id",
            (item_id,),
        )
    )


def list_queue(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(
        connection.execute("SELECT * FROM queue ORDER BY scheduled_at, id")
    )
