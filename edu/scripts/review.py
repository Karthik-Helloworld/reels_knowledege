"""Review pending videos before anything can be published.

``make review`` prints each pending bundle and asks for approve, reject, or
edit. Approve inserts a SQLite row with ``scheduled_at``. Reject does not.
Pass ``--decide`` when stdin is not a TTY. This phase does not publish.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from edu.scripts.pending import (
    approved_root,
    check_item_id,
    format_item,
    list_pending,
    pending_root,
    rejected_root,
    write_caption,
)
from edu.scripts.queue import (
    can_publish,
    configured_cadence,
    connect,
    enqueue,
    isoformat,
    queue_db_path,
    record_decision,
)

_ACTIONS = {
    "a": "approve",
    "approve": "approve",
    "r": "reject",
    "reject": "reject",
    "e": "edit",
    "edit": "edit",
    "s": "skip",
    "skip": "skip",
    "q": "quit",
    "quit": "quit",
}


def _move_bundle(source: Path, destination_root: Path) -> Path:
    destination_root.mkdir(parents=True, exist_ok=True)
    destination = destination_root / source.name
    if destination.exists():
        raise FileExistsError(destination)
    shutil.move(str(source), destination)
    return destination


def approve_item(
    item_id: str,
    *,
    pending: Path,
    approved: Path,
    database: Path,
    now: datetime,
    per_day: float,
) -> datetime:
    check_item_id(item_id)
    source = pending / item_id
    if not source.is_dir():
        raise FileNotFoundError(source)
    script = json.loads((source / "script.json").read_text(encoding="utf-8"))
    topic = str(script.get("topic") or item_id)
    destination = _move_bundle(source, approved)
    connection = connect(database)
    try:
        scheduled = enqueue(
            connection,
            item_id=item_id,
            topic=topic,
            directory=destination,
            now=now,
            per_day=per_day,
        )
    except Exception:
        shutil.move(str(destination), source)
        raise
    finally:
        connection.close()
    return scheduled


def reject_item(item_id: str, *, pending: Path, rejected: Path) -> Path:
    check_item_id(item_id)
    source = pending / item_id
    if not source.is_dir():
        raise FileNotFoundError(source)
    return _move_bundle(source, rejected)


def edit_caption(item_id: str, caption: str, *, pending: Path) -> str:
    check_item_id(item_id)
    source = pending / item_id
    if not source.is_dir():
        raise FileNotFoundError(source)
    previous = (source / "caption.txt").read_text(encoding="utf-8")
    write_caption(source, caption)
    return previous


def _parse_now(raw: str | None) -> datetime:
    if not raw:
        return datetime.now().astimezone()
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--now must include a timezone")
    return parsed


def _print_decision(action: str, item_id: str, scheduled: datetime | None) -> None:
    if action == "approve" and scheduled is not None:
        print(f"Approved {item_id}. scheduled_at={isoformat(scheduled)}")
        return
    if action == "reject":
        print(f"Rejected {item_id}. It was not added to the queue.")
        return
    print(f"Edited {item_id}. It is still pending and cannot be published.")


def decide(
    action: str,
    item_id: str,
    *,
    pending: Path,
    approved: Path,
    rejected: Path,
    database: Path,
    now: datetime,
    per_day: float,
    caption: str | None,
) -> datetime | None:
    scheduled: datetime | None = None
    detail: dict[str, str]
    if action == "approve":
        scheduled = approve_item(
            item_id,
            pending=pending,
            approved=approved,
            database=database,
            now=now,
            per_day=per_day,
        )
        detail = {"scheduled_at": isoformat(scheduled)}
    elif action == "reject":
        reject_item(item_id, pending=pending, rejected=rejected)
        detail = {"result": "rejected"}
    elif action == "edit":
        if caption is None or not caption.strip():
            raise ValueError("edit needs a new caption")
        previous = edit_caption(item_id, caption, pending=pending)
        detail = {"previous_caption": previous, "caption": caption.strip() + "\n"}
    else:
        raise ValueError(f"unknown decision: {action}")
    connection = connect(database)
    try:
        record_decision(
            connection,
            item_id=item_id,
            action=action,
            at=now,
            detail=json.dumps(detail),
        )
    finally:
        connection.close()
    return scheduled


def _interactive(
    *,
    pending: Path,
    approved: Path,
    rejected: Path,
    database: Path,
    now: datetime,
    per_day: float,
) -> int:
    skipped: set[str] = set()
    while True:
        waiting = [item for item in list_pending(pending) if item.name not in skipped]
        if not waiting:
            print("No videos are waiting for review.")
            return 0
        item = waiting[0]
        print(format_item(item))
        answer = input("approve, reject, edit, skip, or quit? ").strip().casefold()
        action = _ACTIONS.get(answer)
        if action is None:
            print("Choose approve, reject, edit, skip, or quit.")
            continue
        if action == "quit":
            return 0
        if action == "skip":
            skipped.add(item.name)
            continue
        caption = None
        if action == "edit":
            caption = input("New caption (empty leaves it unchanged): ")
            if not caption.strip():
                print("Caption unchanged.")
                continue
        scheduled = decide(
            action,
            item.name,
            pending=pending,
            approved=approved,
            rejected=rejected,
            database=database,
            now=now,
            per_day=per_day,
            caption=caption,
        )
        _print_decision(action, item.name, scheduled)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Review pending videos. Nothing is publishable until you approve it."
    )
    parser.add_argument(
        "--decide",
        choices=("approve", "reject", "edit"),
        default=None,
        help="Non-interactive decision. Required when stdin is not a TTY.",
    )
    parser.add_argument("--id", default=None)
    parser.add_argument("--caption", default=None)
    parser.add_argument("--pending", type=Path, default=None)
    parser.add_argument("--approved", type=Path, default=None)
    parser.add_argument("--rejected", type=Path, default=None)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--cadence-per-day", type=float, default=None)
    parser.add_argument("--now", default=None)
    parser.add_argument("--niche", type=Path, default=None)
    args = parser.parse_args(argv)
    pending = args.pending or pending_root()
    approved = args.approved or approved_root()
    rejected = args.rejected or rejected_root()
    database = args.db or queue_db_path()
    try:
        per_day = (
            args.cadence_per_day
            if args.cadence_per_day is not None
            else configured_cadence(args.niche)
        )
        now = _parse_now(args.now)
        if per_day <= 0:
            raise ValueError("cadence_per_day must be positive")
    except (ValueError, OSError) as exc:
        print(f"review: {exc}", file=sys.stderr)
        return 1
    if args.decide is None:
        waiting = list_pending(pending)
        if not waiting:
            print("No videos are waiting for review.")
            return 0
        if not sys.stdin.isatty():
            print(
                "review: pass --decide approve, reject, or edit. "
                "A non-interactive run cannot prompt.",
                file=sys.stderr,
            )
            return 2
        return _interactive(
            pending=pending,
            approved=approved,
            rejected=rejected,
            database=database,
            now=now,
            per_day=per_day,
        )
    item_id = args.id
    if item_id is None:
        waiting = list_pending(pending)
        if len(waiting) != 1:
            print("review: pass --id. More than one video is pending.", file=sys.stderr)
            return 2
        item_id = waiting[0].name
    try:
        scheduled = decide(
            args.decide,
            item_id,
            pending=pending,
            approved=approved,
            rejected=rejected,
            database=database,
            now=now,
            per_day=per_day,
            caption=args.caption,
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(f"review: {exc}", file=sys.stderr)
        return 1
    _print_decision(args.decide, item_id, scheduled)
    if args.decide == "approve":
        connection = connect(database)
        try:
            if not can_publish(connection, item_id):
                print(f"review: {item_id} was not stored as approved", file=sys.stderr)
                return 1
        finally:
            connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
