"""HTTP helper for the source adapters.

Wikipedia and Wikimedia reject clients that do not send a User-Agent.
"""

from __future__ import annotations

import json

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
