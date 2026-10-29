"""Publish due approved videos. Dry-run unless ``--live`` is passed.

``make publish`` calls this module. A dry-run prints the plan and does not
upload. A live Instagram post uploads the mp4, publishes the Reel, then deletes
the hosted file. YouTube uploads the local file with a resumable Shorts session.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from core.utils import repo_root
from edu.niche import niche_path
from edu.publish.envfile import load_env_file, present
from edu.publish.hosting import HostError, delete_video, upload_video
from edu.publish.publish_instagram import (
    InstagramError,
    exchange_long_lived_token,
    publish_reel,
    publishing_quota,
    refresh_before_expiry,
)
from edu.publish.publish_youtube import (
    YouTubeError,
    authorization_url,
    exchange_code,
    load_token,
    refresh_access_token,
    save_token,
    shorts_title,
    upload_short,
    youtube_description,
)
from edu.publish.records import (
    INSTAGRAM_DAILY_LIMIT,
    already_published,
    day_ago,
    due_rows,
    ensure_log,
    load_bundle,
    published_since,
    record_publication,
    run_limit,
)
from edu.scripts.queue import configured_cadence, connect, isoformat
from edu.sources.http import SourceError, make_client


def ai_disclosure_enabled(niche_file: Path | None = None) -> bool:
    raw = os.environ.get("AI_DISCLOSURE", "").strip()
    if raw:
        return raw.casefold() in {"1", "true", "yes", "on"}
    path = niche_file or niche_path()
    loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    if isinstance(loaded, dict) and "ai_disclosure" in loaded:
        return bool(loaded["ai_disclosure"])
    return True


def youtube_refresh_token() -> str:
    token = os.environ.get("YOUTUBE_REFRESH_TOKEN", "").strip()
    if token:
        return token
    return str(load_token().get("refresh_token") or "").strip()


def instagram_token_path() -> Path:
    raw = os.environ.get("IG_TOKEN_PATH", "").strip()
    if raw:
        return Path(raw)
    return repo_root() / "edu" / "out" / "instagram_token.json"


def instagram_access_token() -> str:
    """Prefer the refreshed token file over ``IG_ACCESS_TOKEN``.

    A scheduled run writes that file and reads it next time. Delete the file
    to go back to the value in ``.env``.
    """

    env_token = os.environ.get("IG_ACCESS_TOKEN", "").strip()
    path = instagram_token_path()
    if not path.is_file():
        return env_token
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return env_token
    if not isinstance(payload, dict):
        return env_token
    saved = str(payload.get("access_token") or "").strip()
    return saved or env_token


def instagram_missing() -> list[str]:
    missing = [] if instagram_access_token() else ["IG_ACCESS_TOKEN"]
    return missing + present("IG_USER_ID")


def youtube_missing() -> list[str]:
    missing = present("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET")
    if not youtube_refresh_token():
        missing.append("YOUTUBE_REFRESH_TOKEN")
    return missing


def publish_due(
    *,
    database: Path,
    now: datetime,
    live: bool,
    per_day: float,
    niche_file: Path | None,
    client: httpx.Client,
    sleep,
    item_id: str | None = None,
) -> int:
    connection = connect(database)
    try:
        ensure_log(connection)
        rows = due_rows(connection, now, item_id=item_id)
        if not rows:
            print("publish: nothing is due")
            return 0
        limit = run_limit(per_day)
        failures = 0
        taken = 0
        disclose = ai_disclosure_enabled(niche_file)
        for row in rows:
            if _both_published(connection, str(row["id"])):
                continue
            if taken >= limit:
                print(f"publish: cadence holds {row['id']} until a later run")
                continue
            outcome = _publish_one(
                connection,
                row,
                now=now,
                live=live,
                disclose=disclose,
                client=client,
                sleep=sleep,
            )
            failures += outcome
            taken += 1
        return 1 if failures else 0
    finally:
        connection.close()


def _both_published(connection, item_id: str) -> bool:
    return already_published(connection, item_id, "instagram") and already_published(
        connection, item_id, "youtube"
    )


def _publish_one(connection, row, *, now, live, disclose, client, sleep) -> int:
    try:
        bundle = load_bundle(row)
    except (OSError, ValueError) as exc:
        print(f"publish: {row['id']} could not be read: {exc}", file=sys.stderr)
        return 1
    if not bundle["passed"]:
        print(f"publish: {bundle['id']} fact report did not pass. Skipping.")
        return 1
    failures = 0
    if not already_published(connection, bundle["id"], "instagram"):
        failures += _instagram(
            connection,
            bundle,
            now=now,
            live=live,
            disclose=disclose,
            client=client,
            sleep=sleep,
        )
    if not already_published(connection, bundle["id"], "youtube"):
        failures += _youtube(connection, bundle, now=now, live=live, client=client)
    return 1 if failures else 0


def _instagram(connection, bundle, *, now, live, disclose, client, sleep) -> int:
    missing = instagram_missing()
    title = bundle["topic"]
    if not live or missing:
        why = "dry-run" if not live else "missing " + ", ".join(missing)
        print(f"publish: instagram {bundle['id']} {why}. No upload.")
        if disclose:
            print(f"publish: caption would include the AI label for {title}.")
        return 0
    if published_since(connection, "instagram", day_ago(now)) >= INSTAGRAM_DAILY_LIMIT:
        _fail(connection, bundle, "instagram", now, "local Instagram daily limit reached")
        return 1
    token = instagram_access_token()
    try:
        token = _maybe_refresh_instagram(client, token, now)
        usage, total = publishing_quota(
            client,
            user_id=os.environ["IG_USER_ID"].strip(),
            token=token,
        )
    except (InstagramError, SourceError) as exc:
        _fail(connection, bundle, "instagram", now, str(exc))
        return 1
    if usage >= total:
        _fail(connection, bundle, "instagram", now, "Instagram content publishing limit reached")
        return 1
    hosted = ""
    try:
        payload = Path(bundle["video_path"]).read_bytes()
        hosted = upload_video(client, item_id=bundle["id"], payload=payload, now=now)
        media_id, _sent = publish_reel(
            client,
            user_id=os.environ["IG_USER_ID"].strip(),
            token=token,
            video_url=hosted,
            caption=bundle["caption"],
            ai_disclosure=disclose,
            sleep=sleep,
        )
    except (HostError, InstagramError, SourceError, OSError) as exc:
        _fail(connection, bundle, "instagram", now, str(exc), hosted_url=hosted or None)
        _delete_hosted(client, bundle["id"], now, hosted)
        return 1
    record_publication(
        connection,
        item_id=bundle["id"],
        platform="instagram",
        status="published",
        source_urls=bundle["source_urls"],
        licenses=bundle["licenses"],
        fact_report=bundle["fact_report"],
        published_at=now,
        remote_id=media_id,
        hosted_url=hosted,
    )
    print(f"publish: instagram {bundle['id']} id={media_id}")
    if _delete_hosted(client, bundle["id"], now, hosted):
        return 1
    return 0


def _youtube(connection, bundle, *, now, live, client) -> int:
    missing = youtube_missing()
    title = shorts_title(bundle["topic"])
    if not live or missing:
        why = "dry-run" if not live else "missing " + ", ".join(missing)
        print(f"publish: youtube {bundle['id']} {why}. Title {title!r}. No upload.")
        return 0
    try:
        refreshed = refresh_access_token(
            client,
            client_id=os.environ["YOUTUBE_CLIENT_ID"].strip(),
            client_secret=os.environ["YOUTUBE_CLIENT_SECRET"].strip(),
            refresh_token=youtube_refresh_token(),
        )
        description = youtube_description(
            bundle["caption"],
            bundle["source_urls"],
            bundle["licenses"],
        )
        video_id = upload_short(
            client,
            access_token=str(refreshed["access_token"]),
            title=title,
            description=description,
            payload=Path(bundle["video_path"]).read_bytes(),
        )
    except (YouTubeError, SourceError, OSError) as exc:
        _fail(connection, bundle, "youtube", now, str(exc))
        return 1
    record_publication(
        connection,
        item_id=bundle["id"],
        platform="youtube",
        status="published",
        source_urls=bundle["source_urls"],
        licenses=bundle["licenses"],
        fact_report=bundle["fact_report"],
        published_at=now,
        remote_id=video_id,
    )
    print(f"publish: youtube {bundle['id']} id={video_id}")
    return 0


def _maybe_refresh_instagram(client: httpx.Client, token: str, now: datetime) -> str:
    app_id = os.environ.get("IG_APP_ID", "").strip()
    app_secret = os.environ.get("IG_APP_SECRET", "").strip()
    raw_expiry = os.environ.get("IG_TOKEN_EXPIRES_AT", "").strip()
    if not app_id or not app_secret or not raw_expiry:
        return token
    expiry = datetime.fromisoformat(raw_expiry)
    refreshed = refresh_before_expiry(
        client,
        app_id=app_id,
        app_secret=app_secret,
        token=token,
        expires_at=expiry,
        now=now,
    )
    if refreshed is None:
        return token
    _store_instagram_token(refreshed, now)
    return str(refreshed["access_token"])


def _store_instagram_token(payload: dict, now: datetime) -> None:
    expires_in = int(payload.get("expires_in") or 0)
    path = instagram_token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {
        "access_token": payload.get("access_token"),
        "expires_in": expires_in,
        "refreshed_at": isoformat(now),
    }
    path.write_text(json.dumps(body, indent=2) + "\n", encoding="utf-8")
    print(f"publish: refreshed Instagram token written to {path}")


def _delete_hosted(client, item_id: str, now: datetime, hosted: str) -> int:
    if not hosted:
        return 0
    try:
        delete_video(client, item_id=item_id, now=now)
    except (HostError, SourceError) as exc:
        print(f"publish: could not delete hosted video for {item_id}: {exc}", file=sys.stderr)
        return 1
    print(f"publish: deleted hosted video for {item_id}")
    return 0


def _fail(
    connection,
    bundle,
    platform: str,
    now: datetime,
    detail: str,
    hosted_url: str | None = None,
) -> None:
    print(f"publish: {platform} {bundle['id']} failed: {detail}", file=sys.stderr)
    record_publication(
        connection,
        item_id=bundle["id"],
        platform=platform,
        status="failed",
        source_urls=bundle["source_urls"],
        licenses=bundle["licenses"],
        fact_report=bundle["fact_report"],
        published_at=now,
        hosted_url=hosted_url,
        detail=detail,
    )


def _exchange_instagram(client: httpx.Client) -> int:
    missing = present("IG_APP_ID", "IG_APP_SECRET", "IG_ACCESS_TOKEN")
    if missing:
        print("publish: cannot exchange a token. Missing " + ", ".join(missing), file=sys.stderr)
        return 1
    try:
        payload = exchange_long_lived_token(
            client,
            app_id=os.environ["IG_APP_ID"].strip(),
            app_secret=os.environ["IG_APP_SECRET"].strip(),
            token=os.environ["IG_ACCESS_TOKEN"].strip(),
        )
    except (InstagramError, SourceError) as exc:
        print(f"publish: {exc}", file=sys.stderr)
        return 1
    _store_instagram_token(payload, datetime.now(timezone.utc))
    print("publish: long-lived Instagram token is ready. Copy it into IG_ACCESS_TOKEN.")
    return 0


def _youtube_auth() -> int:
    missing = present("YOUTUBE_CLIENT_ID")
    if missing:
        print("publish: cannot start YouTube OAuth. Missing YOUTUBE_CLIENT_ID", file=sys.stderr)
        return 1
    print(authorization_url(os.environ["YOUTUBE_CLIENT_ID"].strip()))
    print("publish: open that URL, then rerun with --youtube-code. No browser is opened here.")
    return 0


def _youtube_code(client: httpx.Client, code: str) -> int:
    missing = present("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET")
    if missing:
        print(
            "publish: cannot exchange a YouTube code. Missing " + ", ".join(missing),
            file=sys.stderr,
        )
        return 1
    try:
        payload = exchange_code(
            client,
            client_id=os.environ["YOUTUBE_CLIENT_ID"].strip(),
            client_secret=os.environ["YOUTUBE_CLIENT_SECRET"].strip(),
            code=code,
        )
    except (YouTubeError, SourceError) as exc:
        print(f"publish: {exc}", file=sys.stderr)
        return 1
    path = save_token(payload)
    print(f"publish: YouTube refresh token written to {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish due approved videos, or dry-run.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Post for real. A bare make publish stays a dry-run.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not upload or post, even when credentials exist.",
    )
    parser.add_argument("--now", default="")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--id", default="")
    parser.add_argument("--cadence-per-day", type=float, default=None)
    parser.add_argument("--niche", type=Path, default=None)
    parser.add_argument("--env", type=Path, default=None)
    parser.add_argument("--exchange-instagram", action="store_true")
    parser.add_argument("--auth-youtube", action="store_true")
    parser.add_argument("--youtube-code", default="")
    args = parser.parse_args(argv)
    env_path = args.env if args.env is not None else repo_root() / ".env"
    load_env_file(env_path)
    if args.auth_youtube:
        return _youtube_auth()
    with make_client() as client:
        if args.exchange_instagram:
            return _exchange_instagram(client)
        if args.youtube_code:
            return _youtube_code(client, args.youtube_code)
        now = _parse_now(args.now)
        if args.cadence_per_day is not None:
            per_day = args.cadence_per_day
        else:
            per_day = configured_cadence(args.niche)
        database = args.db
        if database is None:
            from edu.scripts.queue import queue_db_path

            database = queue_db_path()
        return publish_due(
            database=database,
            now=now,
            live=bool(args.live and not args.dry_run),
            per_day=per_day,
            niche_file=args.niche,
            client=client,
            sleep=_real_sleep if args.live and not args.dry_run else _noop_sleep,
            item_id=args.id or None,
        )


def _parse_now(raw: str) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SystemExit("publish: --now must include a timezone")
    return parsed.astimezone(timezone.utc)


def _real_sleep(delay: float) -> None:
    import time

    time.sleep(delay)


def _noop_sleep(_delay: float) -> None:
    return None


if __name__ == "__main__":
    raise SystemExit(main())
