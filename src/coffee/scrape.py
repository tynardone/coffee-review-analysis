"""Step 1, ``scrape-reviews``: download new and changed review pages.

:func:`scrape_all_reviews` saves each page to ``data/downloaded/``
(:mod:`coffee.page_store`) and never parses; that is :mod:`coffee.parse`.
Discovery returns every review URL with its sitemap ``<lastmod>`` in about 17
requests, so a run compares that with what is already saved and fetches only
what is new or changed.

A page that cannot be fetched is not saved and is tried again next run. The
rest of the run carries on, and the failure is reported.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

import aiohttp
from tqdm.asyncio import tqdm

from coffee.http_client import HEADERS, fetch
from coffee.page_store import PageStore
from coffee.sitemap import get_review_urls

__all__ = [
    "ScrapeResult",
    "plan_fetch",
    "scrape_all_reviews",
]

logger = logging.getLogger(__name__)


@dataclass
class ScrapeResult:
    saved: int = 0
    failed: list[str] = field(default_factory=list)
    retired: int = 0


def plan_fetch(
    discovered: Mapping[str, date | None], known: Mapping[str, date | None]
) -> tuple[set[str], set[str]]:
    """Split discovered URLs into (to fetch, retired).

    A URL is fetched when it is new, when its sitemap date has moved on, or
    when either date is unknown. An unprovable "unchanged" is treated as
    changed: re-fetching costs one request, while wrongly skipping leaves a
    page permanently stale.

    Retired URLs are held but no longer listed upstream. They are reported and
    never deleted, since a review that has disappeared from the site cannot be
    fetched again and the held copy is the only one.
    """

    def is_stale(url: str, listed: date | None) -> bool:
        if url not in known:
            return True  # never fetched
        held = known[url]
        if listed is None or held is None:
            return True  # freshness unprovable on one side; assume changed
        return listed > held

    to_fetch = {url for url, listed in discovered.items() if is_stale(url, listed)}
    return to_fetch, set(known) - set(discovered)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


async def _fetch_one(
    url: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore
) -> tuple[str, str | None]:
    """Fetch one page, returning None for its HTML on any failure.

    The caller awaits pages one at a time, so an exception escaping here would
    abandon every page still in flight.
    """
    try:
        return url, await fetch(url, session, semaphore)
    except Exception:
        logger.exception("Failed to fetch %s", url)
        return url, None


async def scrape_all_reviews(
    pages: PageStore,
    concurrency: int,
    *,
    full: bool = False,
    limit: int | None = None,
) -> ScrapeResult:
    """Discover review URLs and save every new or changed page.

    ``full`` re-downloads every page regardless of what is saved. ``limit``
    caps how many pages are fetched, for a quick trial run.
    """
    result = ScrapeResult()
    semaphore = asyncio.Semaphore(concurrency)

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        start = time.perf_counter()
        # Raises rather than returning a short list, so a partial discovery
        # cannot look like a complete one.
        discovered = await get_review_urls(session=session, semaphore=semaphore)
        logger.info(
            "Found %d review links in %.2f seconds",
            len(discovered),
            time.perf_counter() - start,
        )

        known = pages.known_lastmods()
        to_fetch, retired = plan_fetch(discovered, known)
        if full:
            to_fetch = set(discovered)
        result.retired = len(retired)
        logger.info(
            "%d saved; fetching %d (%d new, %d changed), skipping %d unchanged",
            len(known),
            len(to_fetch),
            len(to_fetch - set(known)),
            len(to_fetch & set(known)),
            len(discovered) - len(to_fetch),
        )
        if result.retired:
            logger.warning(
                "%d saved review(s) are no longer in the sitemap; keeping them",
                result.retired,
            )

        urls = sorted(to_fetch)[:limit] if limit is not None else sorted(to_fetch)
        tasks = [_fetch_one(url, session, semaphore) for url in urls]
        for future in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
            url, html = await future
            if html is None:
                result.failed.append(url)
                continue
            # Stamped per page, so a page keeps the time it was actually
            # fetched even when a run takes half an hour.
            pages.save(url, html, discovered.get(url), fetched_at=_now())
            result.saved += 1

    if result.failed:
        logger.warning("%d of %d pages failed to fetch", len(result.failed), len(urls))
    return result
