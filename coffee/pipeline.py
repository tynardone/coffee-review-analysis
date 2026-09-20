"""End-to-end scrape: discover every review URL, then fetch and parse each one.

:func:`scrape_all_reviews` writes a dated CSV + JSON to the output directory.
"""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd
from tqdm.asyncio import tqdm

from coffee.config import DATA_DIR, HEADERS
from coffee.review import scrape_review
from coffee.sitemap import get_review_urls

__all__ = [
    "DEFAULT_CONCURRENCY",
    "DEFAULT_OUTPUT_DIR",
    "dated_filename",
    "scrape_all_reviews",
]

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = DATA_DIR / "raw"


def dated_filename(stem: str, suffix: str) -> str:
    """``reviews``, ``csv`` -> ``2026-09-19_reviews.csv``.

    Each run writes its own dated file rather than overwriting the last, so a
    scrape can be compared against its predecessor.
    """
    return f"{datetime.now().strftime('%Y-%m-%d')}_{stem}.{suffix}"


DEFAULT_CONCURRENCY = 10


async def scrape_all_reviews(output_dir: Path, concurrency: int) -> None:
    """Discover every review URL, scrape each review, and save to CSV + JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / dated_filename("reviews", "csv")
    json_path = output_dir / dated_filename("reviews", "json")

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

    if not results:
        logger.warning("No reviews scraped; nothing written.")
        return

    df = pd.DataFrame(results)
    df.to_csv(csv_path, index=False)
    df.to_json(json_path, orient="records")
    logger.info("Wrote %d reviews to %s and %s", len(df), csv_path, json_path)
