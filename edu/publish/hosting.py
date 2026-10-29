"""Upload one approved mp4 to a public URL, then delete it after publishing.

Cloudflare R2 (S3 SigV4) and Supabase Storage are the two hosts. Dry-run
callers must not call these functions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import httpx

from edu.publish.http import request_with_backoff


class HostError(RuntimeError):
    """The public video host refused the upload or the delete."""


def configured_host() -> str:
    chosen = os.environ.get("VIDEO_HOST", "").strip().casefold()
    if chosen:
        return chosen
    if _r2_ready() and not present_r2():
        return "r2"
    if not present_supabase():
        return "supabase"
    return ""


def present_r2() -> list[str]:
    return _blank(
        "R2_ACCESS_KEY_ID",
        "R2_SECRET_ACCESS_KEY",
        "R2_BUCKET",
        "R2_ENDPOINT",
        "R2_PUBLIC_BASE",
    )


def present_supabase() -> list[str]:
    return _blank("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "SUPABASE_BUCKET")


def _blank(*names: str) -> list[str]:
    return [name for name in names if not os.environ.get(name, "").strip()]


def _r2_ready() -> bool:
    return not present_r2()


def public_object_name(item_id: str) -> str:
    return f"{item_id}.mp4"


def upload_video(
    client: httpx.Client,
    *,
    item_id: str,
    payload: bytes,
    now: datetime | None = None,
) -> str:
    host = configured_host()
    name = public_object_name(item_id)
    if host == "r2":
        missing = present_r2()
        if missing:
            raise HostError("R2 is missing " + ", ".join(missing))
        return upload_r2(client, object_name=name, payload=payload, now=now)
    if host == "supabase":
        missing = present_supabase()
        if missing:
            raise HostError("Supabase is missing " + ", ".join(missing))
        return upload_supabase(client, object_name=name, payload=payload)
    raise HostError("set VIDEO_HOST to r2 or supabase before a live Instagram publish")


def delete_video(client: httpx.Client, *, item_id: str, now: datetime | None = None) -> None:
    host = configured_host()
    name = public_object_name(item_id)
    if host == "r2":
        delete_r2(client, object_name=name, now=now)
        return
    if host == "supabase":
        delete_supabase(client, object_name=name)
        return
    raise HostError("set VIDEO_HOST to r2 or supabase before deleting a hosted video")


def upload_supabase(client: httpx.Client, *, object_name: str, payload: bytes) -> str:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    bucket = os.environ["SUPABASE_BUCKET"].strip()
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    url = f"{base}/storage/v1/object/{quote(bucket)}/{quote(object_name)}"
    response = request_with_backoff(
        client,
        "POST",
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "apikey": key,
            "Content-Type": "video/mp4",
            "x-upsert": "true",
        },
        content=payload,
    )
    if response.status_code >= 400:
        raise HostError(f"Supabase upload returned HTTP {response.status_code}")
    return f"{base}/storage/v1/object/public/{quote(bucket)}/{quote(object_name)}"


def delete_supabase(client: httpx.Client, *, object_name: str) -> None:
    base = os.environ["SUPABASE_URL"].rstrip("/")
    bucket = os.environ["SUPABASE_BUCKET"].strip()
    key = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
    url = f"{base}/storage/v1/object/{quote(bucket)}"
    response = request_with_backoff(
        client,
        "DELETE",
        url,
        headers={
            "Authorization": f"Bearer {key}",
            "apikey": key,
            "Content-Type": "application/json",
        },
        content=json_prefixes(object_name),
    )
    if response.status_code >= 400:
        raise HostError(f"Supabase delete returned HTTP {response.status_code}")


def json_prefixes(object_name: str) -> bytes:
    return json.dumps({"prefixes": [object_name]}).encode("utf-8")


def upload_r2(
    client: httpx.Client,
    *,
    object_name: str,
    payload: bytes,
    now: datetime | None = None,
) -> str:
    endpoint = os.environ["R2_ENDPOINT"].rstrip("/")
    bucket = os.environ["R2_BUCKET"].strip()
    url = f"{endpoint}/{quote(bucket)}/{quote(object_name)}"
    headers = sigv4_headers(
        method="PUT",
        url=url,
        payload=payload,
        access_key=os.environ["R2_ACCESS_KEY_ID"].strip(),
        secret_key=os.environ["R2_SECRET_ACCESS_KEY"].strip(),
        now=now,
        content_type="video/mp4",
    )
    response = request_with_backoff(client, "PUT", url, headers=headers, content=payload)
    if response.status_code >= 400:
        raise HostError(f"R2 upload returned HTTP {response.status_code}")
    public = os.environ["R2_PUBLIC_BASE"].rstrip("/")
    return f"{public}/{quote(object_name)}"


def delete_r2(client: httpx.Client, *, object_name: str, now: datetime | None = None) -> None:
    endpoint = os.environ["R2_ENDPOINT"].rstrip("/")
    bucket = os.environ["R2_BUCKET"].strip()
    url = f"{endpoint}/{quote(bucket)}/{quote(object_name)}"
    headers = sigv4_headers(
        method="DELETE",
        url=url,
        payload=b"",
        access_key=os.environ["R2_ACCESS_KEY_ID"].strip(),
        secret_key=os.environ["R2_SECRET_ACCESS_KEY"].strip(),
        now=now,
        content_type=None,
    )
    response = request_with_backoff(client, "DELETE", url, headers=headers, content=b"")
    if response.status_code >= 400:
        raise HostError(f"R2 delete returned HTTP {response.status_code}")


def sigv4_headers(
    *,
    method: str,
    url: str,
    payload: bytes,
    access_key: str,
    secret_key: str,
    now: datetime | None = None,
    content_type: str | None,
    region: str = "auto",
    service: str = "s3",
) -> dict[str, str]:
    stamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    amz_date = stamp.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = stamp.strftime("%Y%m%d")
    parsed = urlparse(url)
    payload_hash = hashlib.sha256(payload).hexdigest()
    canonical_uri = parsed.path or "/"
    headers = {
        "host": parsed.netloc,
        "x-amz-content-sha256": payload_hash,
        "x-amz-date": amz_date,
    }
    if content_type:
        headers["content-type"] = content_type
    signed_names = sorted(headers)
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in signed_names)
    signed_header_names = ";".join(signed_names)
    canonical = "\n".join(
        [
            method.upper(),
            canonical_uri,
            "",
            canonical_headers,
            signed_header_names,
            payload_hash,
        ]
    )
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join(
        [
            "AWS4-HMAC-SHA256",
            amz_date,
            scope,
            hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        ]
    )
    key = _signing_key(secret_key, date_stamp, region, service)
    signature = hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    authorization = (
        "AWS4-HMAC-SHA256 "
        f"Credential={access_key}/{scope}, "
        f"SignedHeaders={signed_header_names}, "
        f"Signature={signature}"
    )
    result = {
        "Authorization": authorization,
        "x-amz-date": amz_date,
        "x-amz-content-sha256": payload_hash,
    }
    if content_type:
        result["Content-Type"] = content_type
    return result


def _signing_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    def _hmac(key: bytes, message: str) -> bytes:
        return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()

    date_key = _hmac(("AWS4" + secret).encode("utf-8"), date_stamp)
    region_key = _hmac(date_key, region)
    service_key = _hmac(region_key, service)
    return _hmac(service_key, "aws4_request")
