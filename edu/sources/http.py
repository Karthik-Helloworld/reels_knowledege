"""HTTP helper for the source adapters.

Wikipedia and Wikimedia reject clients that do not send a User-Agent.
"""

from __future__ import annotations

import json
import time

import httpx

USER_AGENT = (
    "ReelsKnowledge/0.1 (educational local pipeline; "
    "https://github.com/Karthik-Helloworld/reels_knowledege)"
)


class SourceError(RuntimeError):
    """A source API did not return a usable JSON document."""


def client_headers() -> dict[str, str]:
    return {"User-Agent": USER_AGENT, "Accept": "application/json"}


def make_client() -> httpx.Client:
    return httpx.Client(headers=client_headers(), timeout=30.0, follow_redirects=True)


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: dict | None = None,
    attempts: int = 3,
) -> httpx.Response:
    """GET/POST with 2 retries and exponential backoff on connection errors and 5xx."""
    delay = 0.25
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.request(method, url, params=params)
        except httpx.TransportError as exc:
            last_error = exc
            if attempt + 1 == attempts:
                raise SourceError(f"{url} failed after {attempts} attempts: {exc}") from exc
            time.sleep(delay)
            delay *= 2
            continue
        if response.status_code < 500:
            return response
        last_error = SourceError(
            f"{response.request.url.host} returned HTTP {response.status_code}"
        )
        if attempt + 1 == attempts:
            raise last_error
        time.sleep(delay)
        delay *= 2
    raise SourceError(f"{url} failed after {attempts} attempts") from last_error


def read_json(response: httpx.Response) -> dict:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        host = exc.request.url.host
        raise SourceError(f"{host} returned HTTP {exc.response.status_code}") from exc
    try:
        payload = response.json()
    except json.JSONDecodeError as exc:
        raise SourceError(f"{response.request.url.host} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise SourceError(f"{response.request.url.host} returned JSON that is not an object")
    return payload
