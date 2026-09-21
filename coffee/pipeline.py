"""End-to-end scrape: discover review URLs, fetch what changed, store the lot.

Runs incrementally by default. Discovery returns every review URL with its
sitemap ``<lastmod>`` in about 17 requests, so a run can compare that against
what the store holds and fetch only what is new or newer.

``full=True`` ignores what is held and re-fetches everything. This needs to be run if
a parser changes changes the data stored.

:func:`scrape_review` is the unit of work the run is made of: fetch one page,
parse it off the event loop, and return its fields. A page that cannot be
fetched or parsed yields ``None`` rather than raising, so one malformed page
does not abort the run.
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
from coffee.fetch import fetch
from coffee.parser import parse_html
from coffee.sitemap import get_review_urls
from coffee.storage import ReviewStore

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_OUTPUT_DIR",
    "plan_fetch",
    "scrape_all_reviews",
    "scrape_review",
]

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = DATA_DIR / "raw"


DEFAULT_CONCURRENCY = 10


async def scrape_review(
    url: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    retries: int = 5,
) -> dict | None:
    review_page = await fetch(url, session, semaphore, retries=retries)
    if review_page is None:
        return None
    try:
        # Parse off the event loop so CPU-bound parsing overlaps network I/O.
        data = await asyncio.to_thread(parse_html, review_page)
    except Exception:
        # The caller awaits these one at a time, so an exception escaping here
        # would abort the whole run and write nothing. Returning None puts a
        # parse failure on the same footing as a fetch failure, which the
        # caller already counts and reports.
        logging.exception("Failed to parse %s", url)
        return None
    data["url"] = url
    return data


def plan_fetch(
    discovered: Mapping[str, date | None], known: Mapping[str, date | None]
) -> tuple[set[str], set[str]]:
    """Split discovered URLs into (to fetch, retired).

    A URL is fetched when it is new, when its sitemap date has moved on, or
    when either date is unknown. An unprovable "unchanged" is treated as
    changed: re-fetching costs one request, while wrongly skipping leaves a row
    permanently stale.

    Retired URLs are held but no longer listed upstream. They are reported and
    never deleted, since a review that has disappeared from the site cannot be
    re-fetched and the held copy is the only one.
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
        # returning a short list, so a partial discovery cannot produce a
        # dataset that only appears complete.
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
        # The semaphore bounds concurrent requests.
        tasks = [scrape_review(url, session, semaphore) for url in to_fetch]
        for future in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
            # Failed scrapes return None; skipping them keeps all-NaN rows
            # out of the output.
            if (review := await future) is not None:
                # Carried through so a later run can compare freshness.
                review["sitemap_lastmod"] = discovered.get(review["url"])
                # Stamped per row rather than per run, so a carried-forward
                # row keeps the time it was actually fetched.
                review["scraped_at"] = scraped_at
                results.append(review)

    failed = len(to_fetch) - len(results)
    if failed:
        logger.warning("%d of %d reviews failed to scrape", failed, len(to_fetch))

    total = store.replace(results) if full else store.upsert(results)
    logger.info("Corpus now holds %d reviews", total)
