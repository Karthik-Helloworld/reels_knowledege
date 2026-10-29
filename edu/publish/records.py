"""Publish log and the set of approved videos that are due."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from edu.scripts.queue import isoformat

_LOG = """
CREATE TABLE IF NOT EXISTS publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('published', 'failed')),
    source_urls TEXT NOT NULL,
    licenses TEXT NOT NULL,
    fact_report TEXT NOT NULL,
    remote_id TEXT,
    hosted_url TEXT,
    published_at TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
"""

INSTAGRAM_DAILY_LIMIT = 50


def ensure_log(connection: sqlite3.Connection) -> None:
    connection.executescript(_LOG)


def run_limit(per_day: float) -> int:
    """How many due videos one invocation may publish. At least one."""

    if per_day <= 0:
        raise ValueError("cadence_per_day must be positive")
    return max(1, math.ceil(per_day))


def attribution_from_sources(sources: object) -> tuple[list[str], list[str]]:
    """Use the stored source records. Do not guess a license from the caption."""

    if not isinstance(sources, list):
        raise ValueError("sources.json must be a list")
    urls: list[str] = []
    licenses: list[str] = []
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError("each source must be an object")
        url = str(source.get("url") or "").strip()
        license_name = str(source.get("license") or "").strip()
        if not url or not license_name:
            raise ValueError("each source needs a url and a license")
        if url not in urls:
            urls.append(url)
        if license_name not in licenses:
            licenses.append(license_name)
    return urls, licenses


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def load_bundle(row: sqlite3.Row) -> dict:
    script = json.loads(_read(row["script_path"]))
    report_text = _read(row["fact_report_path"])
    report = json.loads(report_text)
    caption = _read(row["caption_path"])
    sources_path = Path(str(row["video_path"])).parent / "sources.json"
    if not sources_path.is_file():
        raise ValueError(f"{sources_path.parent} has no sources.json")
    urls, licenses = attribution_from_sources(json.loads(sources_path.read_text(encoding="utf-8")))
    topic = str(script.get("topic") or row["topic"] or row["id"])
    return {
        "id": str(row["id"]),
        "topic": topic,
        "video_path": str(row["video_path"]),
        "caption": caption,
        "source_urls": urls,
        "licenses": licenses,
        "fact_report": report_text.strip(),
        "passed": bool(isinstance(report, dict) and report.get("passed") is True),
    }


def already_published(connection: sqlite3.Connection, item_id: str, platform: str) -> bool:
    row = connection.execute(
        """
        SELECT 1 FROM publications
        WHERE item_id = ? AND platform = ? AND status = 'published'
        """,
        (item_id, platform),
    ).fetchone()
    return row is not None


def published_since(
    connection: sqlite3.Connection,
    platform: str,
    since: datetime,
) -> int:
    rows = connection.execute(
        """
        SELECT published_at FROM publications
        WHERE platform = ? AND status = 'published'
        """,
        (platform,),
    ).fetchall()
    cutoff = isoformat(since)
    return sum(1 for row in rows if str(row["published_at"]) >= cutoff)


def due_rows(
    connection: sqlite3.Connection,
    now: datetime,
    *,
    item_id: str | None = None,
) -> list[sqlite3.Row]:
    stamp = isoformat(now)
    if item_id:
        rows = connection.execute(
            """
            SELECT * FROM queue
            WHERE id = ? AND status = 'approved' AND scheduled_at <= ?
            """,
            (item_id, stamp),
        ).fetchall()
    else:
        rows = connection.execute(
            """
            SELECT * FROM queue
            WHERE status = 'approved' AND scheduled_at <= ?
            ORDER BY scheduled_at, id
            """,
            (stamp,),
        ).fetchall()
    return list(rows)


def record_publication(
    connection: sqlite3.Connection,
    *,
    item_id: str,
    platform: str,
    status: str,
    source_urls: list[str],
    licenses: list[str],
    fact_report: str,
    published_at: datetime,
    remote_id: str | None = None,
    hosted_url: str | None = None,
    detail: str = "",
) -> None:
    connection.execute(
        """
        INSERT INTO publications (
            item_id, platform, status, source_urls, licenses, fact_report,
            remote_id, hosted_url, published_at, detail
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            item_id,
            platform,
            status,
            json.dumps(source_urls),
            json.dumps(licenses),
            fact_report,
            remote_id,
            hosted_url,
            isoformat(published_at),
            detail,
        ),
    )
    connection.commit()


def day_ago(now: datetime) -> datetime:
    return now - timedelta(days=1)
