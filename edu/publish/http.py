"""HTTP retries for the publishers.

Transport errors, HTTP 429, and HTTP 5xx are retried. A 429 waits for
``Retry-After`` when that header is a number of seconds. Other 4xx responses,
including an Instagram field the server rejects, are returned immediately.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx

from edu.sources.http import SourceError


def request_with_backoff(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    attempts: int = 3,
    sleep: Callable[[float], None] | None = None,
    **kwargs: object,
) -> httpx.Response:
    pause = time.sleep if sleep is None else sleep
    delay = 0.25
    last_error: Exception | None = None
    for attempt in range(attempts):
        try:
            response = client.request(method, url, **kwargs)  # type: ignore[arg-type]
        except httpx.TransportError as exc:
            last_error = exc
            if attempt + 1 == attempts:
                raise SourceError(f"{url} failed after {attempts} attempts: {exc}") from exc
            pause(delay)
            delay *= 2
            continue
        if response.status_code != 429 and response.status_code < 500:
            return response
        if attempt + 1 == attempts:
            return response
        retry_after = response.headers.get("retry-after", "").strip()
        wait = float(retry_after) if retry_after.isdigit() else delay
        pause(wait)
        delay *= 2
    raise SourceError(f"{url} failed after {attempts} attempts") from last_error
