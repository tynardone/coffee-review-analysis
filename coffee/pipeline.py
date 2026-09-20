"""End-to-end scrape: discover review URLs, fetch what changed, store the lot.

Runs INCREMENTALLY by default. Discovery returns every review URL with its
sitemap ``<lastmod>`` for about 17 requests, so the run can compare that against
what the store already holds and fetch only what is new or newer. On the current
corpus that is roughly 300 pages instead of 9,300 — about a minute rather than
half an hour — and a monthly cadence brings it down to ~50.

Pass ``full=True`` to ignore what is held and re-fetch everything. That is the
escape hatch for a parser change: incremental re-parses only the pages it
re-fetches, so a fix to :mod:`coffee.parser` reaches old rows only on a full
run. ``scraped_at`` records when each row was last fetched, which is what makes
that mixture visible rather than silent.
"""

import asyncio
import logging
import time
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

import aiohttp
from tqdm.asyncio import tqdm

from coffee.config import DATA_DIR, HEADERS
from coffee.review import scrape_review
from coffee.sitemap import get_review_urls
from coffee.storage import ReviewStore

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_OUTPUT_DIR",
    "plan_fetch",
    "scrape_all_reviews",
]

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = DATA_DIR / "raw"


DEFAULT_CONCURRENCY = 10


def plan_fetch(
    discovered: Mapping[str, date | None], known: Mapping[str, date | None]
) -> tuple[set[str], set[str]]:
    """Split discovered URLs into (to fetch, retired).

    A URL is fetched when it is new, when its sitemap date has moved on, or
    when either date is unknown — an unprovable "unchanged" is treated as
    changed, because the cost of re-fetching a page is one request and the cost
    of wrongly skipping it is a row that is quietly wrong forever.

    "Retired" URLs are held but no longer listed upstream. They are reported,
    never deleted: a review that disappears from the site cannot be re-fetched,
    so dropping it would destroy the only copy.
    """

    def is_stale(url: str, listed: date | None) -> bool:
        if url not in known:
            return True  # never scraped
        held = known[url]
        if listed is None or held is None:
            return True  # freshness unprovable on one side; assume changed
        return listed > held

    to_fetch = {url for url, listed in discovered.items() if is_stale(url, listed)}
    return to_fetch, set(known) - set(discovered)


async def scrape_all_reviews(
    store: ReviewStore, concurrency: int, *, full: bool = False
) -> None:
    """Discover, fetch what changed (or everything), and store the results."""
    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any]] = []

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        start = time.perf_counter()
        # Maps each review URL to its sitemap <lastmod>. Raises rather than
        # returning a short list, so a partial discovery can't quietly produce a
        # dataset that merely looks complete.
        discovered = await get_review_urls(session=session, semaphore=semaphore)
        logger.info(
            "Found %d review links in %.2f seconds",
            len(discovered),
            time.perf_counter() - start,
        )

        if full:
            to_fetch: set[str] = set(discovered)
            retired: set[str] = set()
            logger.info("Full run: fetching all %d reviews", len(to_fetch))
        else:
            known = store.known_lastmods()
            to_fetch, retired = plan_fetch(discovered, known)
            logger.info(
                "Held %d; fetching %d (%d new, %d changed), skipping %d unchanged",
                len(known),
                len(to_fetch),
                len(to_fetch - set(known)),
                len(to_fetch & set(known)),
                len(discovered) - len(to_fetch),
            )
        if retired:
            logger.warning(
                "%d held review(s) are no longer in the sitemap; keeping them",
                len(retired),
            )

        if not to_fetch:
            logger.info("Nothing to fetch; the corpus is already current.")
            return

        scraped_at = datetime.now().isoformat(timespec="seconds")
        # The semaphore bounds concurrent requests while still improving on
        # pure-synchronous scraping.
        tasks = [scrape_review(url, session, semaphore) for url in to_fetch]
        for future in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
            # Failed scrapes return None; skip them so they don't become
            # all-NaN rows in the output.
            if (review := await future) is not None:
                # Carried through so a later run can re-scrape only what changed.
                review["sitemap_lastmod"] = discovered.get(review["url"])
                # Per ROW, not per run: a carried-forward row keeps the stamp
                # from when it was actually fetched, so row age stays readable.
                review["scraped_at"] = scraped_at
                results.append(review)

    failed = len(to_fetch) - len(results)
    if failed:
        logger.warning("%d of %d reviews failed to scrape", failed, len(to_fetch))

    total = store.replace(results) if full else store.upsert(results)
    logger.info("Corpus now holds %d reviews", total)
