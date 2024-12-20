"""Main script."""

import asyncio
import logging
import time
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd
from tqdm.asyncio import tqdm

from src.async_review_scraper import scrape_review
from src.async_url_scraper import get_urls
from src.config import Config
from src.utils import create_filename

DATA_DIR: Path = Config.BASEDIR / Path("data/raw/")
SEMAPHORE_COUNT: int = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger()


async def main() -> None:
    # Create the filepaths for saving the data
    csv_filepath: Path = DATA_DIR / create_filename("reviews", "csv")
    json_filepath: Path = DATA_DIR / create_filename("reviews", "json")

    # Initialize the results list
    results: list[dict[Any, Any] | None] = []

    # Create an aiohttp ClientSession.
    semaphore = asyncio.Semaphore(SEMAPHORE_COUNT)
    async with aiohttp.ClientSession(headers=Config.HEADERS) as session:
        start: float = time.time()
        # Scrape website for review urls
        urls: set[str] = await get_urls(base_url=Config.BASE_URL, session=session)
        end: float = time.time()
        logger.info(f"Time elapsed: {end - start:.2f} seconds")
        logger.info(f"Total review links found: {len(urls)}")
        # Uses Semaphore to limit the number of concurrent requests while scraping
        # reviews, while still allowing speed improvements
        # over pure synchronous scraping
        tasks = [scrape_review(url, session, semaphore) for url in urls]
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
            result = await f
            results.append(result)

    # Use pandas to save the results to a CSV and JSON file
    df = pd.DataFrame(results)
    df.to_csv(csv_filepath, index=False)
    df.to_json(json_filepath, orient="records")


if __name__ == "__main__":
    asyncio.run(main())
