"""Main script."""

import asyncio
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import aiohttp
import pandas as pd
from tqdm.asyncio import tqdm

from src.config import Config
from src.async_review_scraper import scrape_review
from src.async_url_scraper import get_urls


def create_filename(filename: str, filetype: str) -> str:
    """Creates a filename of format 'ddmmyyyy_filename.filetype'"""
    current_date: str = datetime.now().strftime("%d%m%Y")
    filename = f"{current_date}_{filename}.{filetype}"
    return filename


def create_filepath(filename: str, filetype: str) -> Path:
    """Creates a filepath for the file in the 'data/raw' directory."""
    if filetype not in ("csv", "json", "pkl"):
        raise ValueError(
            "Invalid file type. Only 'csv', 'json', and 'pkl' are supported."
        )
    data_dir = Path("data/raw/")
    return data_dir / create_filename(filename, filetype)


async def main() -> None:
    # Create the filepaths for saving the data
    csv_filepath: Path = create_filepath("reviews", "csv")
    json_filepath: Path = create_filepath("reviews", "json")

    # Initialize the results list
    results: list[dict[Any, Any] | None] = []

    # Create an aiohttp ClientSession.
    semaphore = asyncio.Semaphore(10)
    async with aiohttp.ClientSession(headers=Config.HEADERS) as session:
        start: float = time.time()
        # Scrape website for review urls
        urls: set[str] = await get_urls(base_url=Config.BASE_URL, session=session)
        end: float = time.time()
        print(f"Time elapsed: {end - start:.2f} seconds")
        print(f"Total review links found: {len(urls)}")
        # Uses Semaphore to limit the number of concurrent requests while scraping reviews,
        # while still allowing speed improvements over pure synchronous scraping
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
