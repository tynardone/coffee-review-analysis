"""The one place that talks HTTP: shared clients, headers and retry policy.

Every request in the project goes through here: the scrape and sitemap
discovery asynchronously with :func:`fetch`, and the two reference-data APIs
synchronously with :func:`request_json`. Both follow the same rules:

- a timeout on every request
- retry only what is transient (connection errors, timeouts, 429 and 5xx), with
  exponential backoff and jitter, honoring a numeric ``Retry-After``
- never retry a permanent error such as 404 or 401; it will not fix itself

The requests identify the project honestly rather than posing as a browser.
"""

import asyncio
import logging
import random
import time
from collections.abc import Callable, Mapping
from typing import Any, Final

import httpx

__all__ = [
    "HEADERS",
    "async_client",
    "fetch",
    "request_json",
    "retry_delay",
    "sync_client",
]

logger = logging.getLogger(__name__)

# Sent with every request. A project URL rather than a personal address, since
# the sites see it.
HEADERS: Final[Mapping[str, str]] = {
    "user-agent": (
        "coffee-review-analysis/1.0 "
        "(+https://github.com/tynardone/coffee-review-analysis)"
    )
}

TIMEOUT: Final = httpx.Timeout(20.0)

# Only transient failures are retried. Other 4xx responses, such as a 404 for a
# removed review, are permanent and fail fast rather than consuming retries.
RETRY_STATUSES: Final = frozenset({429, 500, 502, 503, 504})
BASE_DELAY: Final = 1.0  # seconds; exponential backoff base
MAX_DELAY: Final = 30.0
JITTER: Final = 1.0


def retry_delay(attempt: int, retry_after: str | None) -> float:
    """Exponential backoff with jitter, honoring a numeric Retry-After header."""
    if retry_after and retry_after.isdigit():
        return float(retry_after)
    return min(BASE_DELAY * 2**attempt, MAX_DELAY) + random.uniform(0, JITTER)


def async_client() -> httpx.AsyncClient:
    """A client for the scrape; use it as ``async with async_client() as c``."""
    return httpx.AsyncClient(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)


def sync_client() -> httpx.Client:
    """A client for the reference APIs; use it as ``with sync_client() as c``."""
    return httpx.Client(headers=HEADERS, timeout=TIMEOUT, follow_redirects=True)


async def fetch(
    url: str,
    client: httpx.AsyncClient,
    semaphore: asyncio.Semaphore,
    retries: int = 5,
) -> str | None:
    """A page's text, or None once transient retries run out or on a
    permanent error.

    The semaphore caps how many requests are in flight at once. It is held
    only for the request itself, not during backoff, so a slow-failing URL
    does not keep a slot idle.
    """
    for attempt in range(retries):
        delay: float | None = None
        try:
            async with semaphore:
                response = await client.get(url)
            if response.status_code == 200:
                return response.text
            if response.status_code not in RETRY_STATUSES:
                logger.warning("Skipping %s (HTTP %d)", url, response.status_code)
                return None
            delay = retry_delay(attempt, response.headers.get("Retry-After"))
        except httpx.TransportError:
            delay = retry_delay(attempt, None)

        if attempt < retries - 1:
            await asyncio.sleep(delay)

    logger.error("Failed to fetch %s after %d attempts.", url, retries)
    return None


def request_json(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    params: Mapping[str, str] | None = None,
    json: Any = None,
    timeout: float | None = None,
    retries: int = 3,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    """Send a request and return the decoded JSON body.

    Transient failures are retried; anything else, or running out of retries,
    raises :class:`httpx.HTTPError` so the caller decides what a failure means.
    """
    for attempt in range(retries):
        last_attempt = attempt == retries - 1
        try:
            response = client.request(
                method,
                url,
                params=params,
                json=json,
                timeout=timeout if timeout is not None else TIMEOUT,
            )
        except httpx.TransportError:
            if last_attempt:
                raise
            sleep(retry_delay(attempt, None))
            continue
        if response.status_code in RETRY_STATUSES and not last_attempt:
            sleep(retry_delay(attempt, response.headers.get("Retry-After")))
            continue
        response.raise_for_status()
        return response.json()
    raise AssertionError("unreachable: the last attempt returns or raises")
