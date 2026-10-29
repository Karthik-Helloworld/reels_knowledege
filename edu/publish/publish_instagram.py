"""Instagram Graph API Reels.

Create a container with ``video_url``, poll ``status_code`` until ``FINISHED``,
then ``media_publish``. A short-lived token can be exchanged for a long-lived
token and refreshed before it expires. Dry-run callers do not import a session
or open a browser. There is no instagrapi.
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import httpx

from edu.publish.http import request_with_backoff

AI_CAPTION_LINE = "AI disclosure: this video was made with AI tools."
GRAPH_VERSION = "v21.0"
REFRESH_WITHIN = timedelta(days=7)


class InstagramError(RuntimeError):
    """Graph API rejected a Reel or the token exchange."""


def graph_base() -> str:
    version = os.environ.get("IG_GRAPH_VERSION", "").strip() or GRAPH_VERSION
    return f"https://graph.facebook.com/{version}"


def append_disclosure(caption: str) -> str:
    if AI_CAPTION_LINE in caption:
        return caption if caption.endswith("\n") else caption + "\n"
    return caption.rstrip() + "\n\n" + AI_CAPTION_LINE + "\n"


def exchange_long_lived_token(
    client: httpx.Client,
    *,
    app_id: str,
    app_secret: str,
    token: str,
) -> dict:
    """Exchange a short-lived token, or refresh a long-lived one, for ~60 days."""

    response = request_with_backoff(
        client,
        "GET",
        f"{graph_base()}/oauth/access_token",
        params={
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": token,
        },
    )
    payload = _object(response)
    if response.status_code >= 400 or "access_token" not in payload:
        raise InstagramError("Instagram token exchange failed")
    return payload


def refresh_before_expiry(
    client: httpx.Client,
    *,
    app_id: str,
    app_secret: str,
    token: str,
    expires_at: datetime,
    now: datetime,
) -> dict | None:
    """Refresh when the long-lived token expires within seven days.

    Returns None when the token is still fresh. Raises when it has already
    expired, because Graph will not refresh a dead token.
    """

    expiry = _aware(expires_at)
    current = _aware(now)
    if expiry <= current:
        raise InstagramError("Instagram token is expired. Exchange a new long-lived token.")
    if expiry - current > REFRESH_WITHIN:
        return None
    return exchange_long_lived_token(client, app_id=app_id, app_secret=app_secret, token=token)


def publishing_quota(client: httpx.Client, *, user_id: str, token: str) -> tuple[int, int]:
    response = request_with_backoff(
        client,
        "GET",
        f"{graph_base()}/{user_id}/content_publishing_limit",
        params={"fields": "quota_usage,config", "access_token": token},
    )
    payload = _object(response)
    if response.status_code >= 400:
        raise InstagramError("Instagram publishing limit check failed")
    rows = payload.get("data")
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        raise InstagramError("Instagram publishing limit was not a list")
    row = rows[0]
    config = row.get("config")
    if not isinstance(config, dict):
        raise InstagramError("Instagram publishing limit had no config")
    return int(row["quota_usage"]), int(config["quota_total"])


def publish_reel(
    client: httpx.Client,
    *,
    user_id: str,
    token: str,
    video_url: str,
    caption: str,
    ai_disclosure: bool,
    sleep,
) -> tuple[str, str]:
    """Publish one Reel. Returns ``(media id, caption that was sent)``."""

    sent = caption
    fields = {
        "media_type": "REELS",
        "video_url": video_url,
        "caption": sent,
        "access_token": token,
    }
    if ai_disclosure:
        fields["is_ai_generated"] = "true"
    response = request_with_backoff(
        client,
        "POST",
        f"{graph_base()}/{user_id}/media",
        data=fields,
    )
    if ai_disclosure and _rejected_ai_field(response):
        sent = append_disclosure(caption)
        fields.pop("is_ai_generated")
        fields["caption"] = sent
        response = request_with_backoff(
            client,
            "POST",
            f"{graph_base()}/{user_id}/media",
            data=fields,
        )
    container = _object(response)
    if response.status_code >= 400 or "id" not in container:
        raise InstagramError("Instagram did not create a Reel container")
    container_id = str(container["id"])
    _poll_container(client, container_id=container_id, token=token, sleep=sleep)
    published = request_with_backoff(
        client,
        "POST",
        f"{graph_base()}/{user_id}/media_publish",
        data={"creation_id": container_id, "access_token": token},
    )
    media = _object(published)
    if published.status_code >= 400 or "id" not in media:
        raise InstagramError("Instagram media_publish failed")
    return str(media["id"]), sent


def _poll_container(client: httpx.Client, *, container_id: str, token: str, sleep) -> None:
    for _attempt in range(10):
        response = request_with_backoff(
            client,
            "GET",
            f"{graph_base()}/{container_id}",
            params={"fields": "status_code", "access_token": token},
        )
        payload = _object(response)
        status = str(payload.get("status_code") or "")
        if status == "FINISHED":
            return
        if status in {"ERROR", "EXPIRED"}:
            raise InstagramError(f"Instagram container {status}")
        if response.status_code >= 400:
            raise InstagramError("Instagram container status failed")
        sleep(1.0)
    raise InstagramError("Instagram container was not ready")


def _rejected_ai_field(response: httpx.Response) -> bool:
    if response.status_code < 400:
        return False
    return "is_ai_generated" in response.text.casefold()


def _object(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InstagramError("token expiry must include a timezone")
    return value.astimezone(timezone.utc)
