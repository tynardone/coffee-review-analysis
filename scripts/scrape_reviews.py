"""Scrape all coffee reviews from CoffeeReview.com to CSV and JSON.

Discovers every review URL, scrapes each review concurrently, and writes a
dated CSV + JSON to the output directory.
"""

import argparse
import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd
from tqdm.asyncio import tqdm

from coffee.async_review_scraper import scrape_review
from coffee.async_url_scraper import get_urls
from coffee.config import Config
from coffee.utils import create_filename

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_DIR = Config.DATA_DIR / "raw"
DEFAULT_CONCURRENCY = 10


async def scrape_reviews(output_dir: Path, concurrency: int) -> None:
    """Discover every review URL, scrape each review, and save to CSV + JSON."""
    output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = output_dir / create_filename("reviews", "csv")
    json_path = output_dir / create_filename("reviews", "json")

    semaphore = asyncio.Semaphore(concurrency)
    results: list[dict[str, Any]] = []

    async with aiohttp.ClientSession(headers=Config.HEADERS) as session:
        start = time.perf_counter()
        urls = await get_urls(base_url=Config.BASE_URL, session=session)
        logger.info(
            "Found %d review links in %.2f seconds",
            len(urls),
            time.perf_counter() - start,
        )

        # The semaphore bounds concurrent requests while still improving on
        # pure-synchronous scraping.
        tasks = [scrape_review(url, session, semaphore) for url in urls]
        for future in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
            # Failed scrapes return None; skip them so they don't become
            # all-NaN rows in the output.
            if (review := await future) is not None:
                results.append(review)

    failed = len(urls) - len(results)
    if failed:
        logger.warning("%d of %d reviews failed to scrape", failed, len(urls))

    df = pd.DataFrame(results)
    df.to_csv(csv_path, index=False)
    df.to_json(json_path, orient="records")
    logger.info("Wrote %d reviews to %s and %s", len(df), csv_path, json_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-o",
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help="Directory for the dated reviews CSV and JSON.",
    )
    parser.add_argument(
        "-c",
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help="Maximum number of concurrent review requests.",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )
    args = parse_args()
    asyncio.run(scrape_reviews(args.output_dir, args.concurrency))


if __name__ == "__main__":
    main()
