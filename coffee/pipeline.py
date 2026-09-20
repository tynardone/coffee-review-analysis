"""End-to-end scrape: discover every review URL, then fetch and parse each one.

:func:`scrape_all_reviews` hands the results to a :class:`~coffee.storage.
ReviewStore` rather than writing files itself, so where reviews land is the
caller's choice and not baked into the pipeline.
"""

import asyncio
import logging
import time
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
    "scrape_all_reviews",
]

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = DATA_DIR / "raw"


DEFAULT_CONCURRENCY = 10


async def scrape_all_reviews(store: ReviewStore, concurrency: int) -> None:
    """Discover every review URL, scrape each review, and hand them to `store`."""
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

        # The semaphore bounds concurrent requests while still improving on
        # pure-synchronous scraping.
        tasks = [scrape_review(url, session, semaphore) for url in discovered]
        for future in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
            # Failed scrapes return None; skip them so they don't become
            # all-NaN rows in the output.
            if (review := await future) is not None:
                # Carried through so a later run can re-scrape only what changed.
                review["sitemap_lastmod"] = discovered.get(review["url"])
                results.append(review)

    failed = len(discovered) - len(results)
    if failed:
        logger.warning("%d of %d reviews failed to scrape", failed, len(discovered))

    store.save(results)
