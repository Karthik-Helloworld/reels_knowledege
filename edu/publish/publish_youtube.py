"""YouTube Data API v3 resumable upload for Shorts.

The desktop OAuth flow prints a URL and accepts a pasted code. The refresh
token stays in a local file under ``edu/out/``, which is gitignored. Nothing
here opens a browser. Dry-run does not call Google.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlencode

import httpx

from core.utils import repo_root
from edu.publish.http import request_with_backoff

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
SCOPE = "https://www.googleapis.com/auth/youtube.upload"
REDIRECT_URI = "http://127.0.0.1:8765/"
TITLE_LIMIT = 100
SHORTS_SUFFIX = " #Shorts"


class YouTubeError(RuntimeError):
    """OAuth or the resumable upload failed."""


def shorts_title(topic: str) -> str:
    text = " ".join(topic.split()) or "Science fact"
    room = TITLE_LIMIT - len(SHORTS_SUFFIX)
    if len(text) > room:
        text = text[: room - 1].rstrip() + "…"
    title = text + SHORTS_SUFFIX
    if len(title) > TITLE_LIMIT or "#Shorts" not in title:
        raise YouTubeError("Shorts title could not be built")
    return title


def youtube_description(caption: str, source_urls: list[str], licenses: list[str]) -> str:
    text = caption.strip()
    missing = [url for url in source_urls if url not in text]
    missing_licenses = [name for name in licenses if name not in text]
    lines = [text]
    if missing or missing_licenses:
        lines.append("")
        lines.append("Sources:")
        lines.extend(missing)
        lines.extend(missing_licenses)
    return "\n".join(lines).rstrip() + "\n"


def authorization_url(
    client_id: str,
    *,
    redirect_uri: str = REDIRECT_URI,
    state: str = "reels",
) -> str:
    query = urlencode(
        {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": SCOPE,
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
    )
    return f"{AUTH_URL}?{query}"


def token_path() -> Path:
    raw = os.environ.get("YOUTUBE_TOKEN_PATH", "").strip()
    if raw:
        return Path(raw)
    return repo_root() / "edu" / "out" / "youtube_token.json"


def save_token(payload: dict, path: Path | None = None) -> Path:
    destination = path or token_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return destination


def load_token(path: Path | None = None) -> dict:
    destination = path or token_path()
    if not destination.is_file():
        return {}
    payload = json.loads(destination.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise YouTubeError("YouTube token file must be a JSON object")
    return payload


def exchange_code(
    client: httpx.Client,
    *,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str = REDIRECT_URI,
) -> dict:
    response = request_with_backoff(
        client,
        "POST",
        TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
    )
    payload = _object(response)
    if response.status_code >= 400 or "refresh_token" not in payload:
        raise YouTubeError("YouTube code exchange failed")
    return payload


def refresh_access_token(
    client: httpx.Client,
    *,
    client_id: str,
    client_secret: str,
    refresh_token: str,
) -> dict:
    response = request_with_backoff(
        client,
        "POST",
        TOKEN_URL,
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
    )
    payload = _object(response)
    if response.status_code >= 400 or "access_token" not in payload:
        raise YouTubeError("YouTube token refresh failed")
    payload.setdefault("refresh_token", refresh_token)
    return payload


def upload_short(
    client: httpx.Client,
    *,
    access_token: str,
    title: str,
    description: str,
    payload: bytes,
) -> str:
    if len(title) > TITLE_LIMIT or "#Shorts" not in title:
        raise YouTubeError("YouTube title must be at most 100 characters and include #Shorts")
    if "http://" not in description and "https://" not in description:
        raise YouTubeError("YouTube description is missing a source URL")
    location = _start_session(
        client,
        access_token=access_token,
        title=title,
        description=description,
        size=len(payload),
    )
    media = _put_resumable(client, location, payload)
    video_id = str(media.get("id") or "")
    if not video_id:
        raise YouTubeError("YouTube upload did not return a video id")
    return video_id


def _start_session(
    client: httpx.Client,
    *,
    access_token: str,
    title: str,
    description: str,
    size: int,
) -> str:
    body = {
        "snippet": {"title": title, "description": description, "categoryId": "27"},
        "status": {"privacyStatus": "public", "selfDeclaredMadeForKids": False},
    }
    response = request_with_backoff(
        client,
        "POST",
        UPLOAD_URL,
        params={"uploadType": "resumable", "part": "snippet,status"},
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(size),
            "X-Upload-Content-Type": "video/mp4",
        },
        content=json.dumps(body).encode("utf-8"),
    )
    if response.status_code not in {200, 201}:
        raise YouTubeError(f"YouTube resumable session returned HTTP {response.status_code}")
    location = response.headers.get("location", "").strip()
    if not location:
        raise YouTubeError("YouTube resumable session did not return Location")
    return location


def _put_resumable(client: httpx.Client, location: str, payload: bytes) -> dict:
    start = 0
    while start < len(payload):
        chunk = payload[start:]
        headers = {
            "Content-Type": "video/mp4",
            "Content-Length": str(len(chunk)),
        }
        if start:
            end = len(payload) - 1
            headers["Content-Range"] = f"bytes {start}-{end}/{len(payload)}"
        response = request_with_backoff(
            client,
            "PUT",
            location,
            headers=headers,
            content=chunk,
        )
        if response.status_code in {200, 201}:
            payload_json = _object(response)
            if not payload_json:
                raise YouTubeError("YouTube upload returned an empty body")
            return payload_json
        if response.status_code == 308:
            start = _resume_at(response)
            continue
        raise YouTubeError(f"YouTube upload returned HTTP {response.status_code}")
    raise YouTubeError("YouTube upload did not finish")


def _resume_at(response: httpx.Response) -> int:
    header = response.headers.get("range", "")
    if "-" not in header:
        raise YouTubeError("YouTube resume did not include Range")
    last = header.rsplit("-", 1)[1]
    return int(last) + 1


def _object(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    if isinstance(payload, dict):
        return payload
    return {}
