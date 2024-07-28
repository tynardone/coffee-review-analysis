"""Main script to scrape reviews from the website and save them to a file."""

import logging
from datetime import datetime
from pathlib import Path
import time

import asyncio
import aiohttp
from tqdm.asyncio import tqdm
import pandas as pd

from src.async_url_scraper import get_urls
from src.async_review_scraper import scrape_review
import src.config as config


def create_filename(filename: str, filetype: str) -> str:
    """Creates a filename of format 'ddmmyyyy_filename.filetype'"""
    assert filetype in ["csv", "json", "pkl"], "Invalid file type"
    date = datetime.now().strftime("%d%m%Y")
    filename = f"{date}_{filename}.{filetype}"
    return filename


def create_filepath(filename: str, filetype: str) -> Path:
    """Creates a filepath for the file in the 'data/raw' directory."""
    data_dir = Path("data/raw/")
    return data_dir / create_filename(filename, filetype)


async def main() -> None:
    # Create the filepaths for saving the data
    csv_filepath = create_filepath("reviews", "csv")
    json_filepath = create_filepath("reviews", "json")

    # Initialize the results list
    results = []

    # Create an aiohttp ClientSession.
    semaphore = asyncio.Semaphore(10)
    async with aiohttp.ClientSession(headers=config.HEADERS) as session:
        start = time.time()
        # Scrape website for review urls
        urls = await get_urls(base_url=config.BASE_URL, session=session)
        end = time.time()
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
