"""Shared async HTTP GET with bounded concurrency and retry.

:func:`fetch` gives URL discovery and review scraping one common request path:
a per-request timeout, retries limited to transient failures (429/5xx) with
exponential backoff and jitter honouring ``Retry-After``, and backoff performed
outside the caller's semaphore so that a slow-failing URL does not hold a
concurrency slot idle. Permanent errors such as 404 return ``None`` at once.
"""

import asyncio
import logging
import random

import aiohttp

__all__ = ["fetch"]  # the module-level constants are tuning knobs, not API

# Only transient failures are retried. Other 4xx responses, such as a 404 for a
# removed review, are permanent and fail fast rather than consuming retries.
RETRY_STATUSES: frozenset[int] = frozenset({429, 500, 502, 503, 504})
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
BASE_DELAY = 1.0  # seconds; exponential backoff base
MAX_DELAY = 30.0
JITTER = 1.0


def _retry_delay(attempt: int, retry_after: str | None) -> float:
    """Exponential backoff with jitter, honouring a numeric Retry-After header."""
    if retry_after and retry_after.isdigit():
        return float(retry_after)
    return min(BASE_DELAY * 2**attempt, MAX_DELAY) + random.uniform(0, JITTER)


async def fetch(
    url: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    retries: int = 5,
) -> str | None:
    """Fetch a URL with bounded concurrency, retrying only transient failures.

    The semaphore is held only for the request itself, not during backoff
    sleeps, so a slow-failing URL does not hold a concurrency slot idle.
    """
    for attempt in range(retries):
        delay: float | None = None
        try:
            async with semaphore:
                async with session.get(url, timeout=REQUEST_TIMEOUT) as response:
                    if response.status == 200:
                        return await response.text()
                    if response.status not in RETRY_STATUSES:
                        logging.warning("Skipping %s (HTTP %d)", url, response.status)
                        return None
                    delay = _retry_delay(attempt, response.headers.get("Retry-After"))
        except (aiohttp.ClientError, asyncio.TimeoutError):
            delay = _retry_delay(attempt, None)

        if delay is not None and attempt < retries - 1:
            await asyncio.sleep(delay)

    logging.error("Failed to fetch %s after %d attempts.", url, retries)
    return None
