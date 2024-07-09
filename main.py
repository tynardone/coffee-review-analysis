"""Main script to scrape reviews from the website and save them to a file."""

import logging
from datetime import datetime
from pathlib import Path

import asyncio
import pandas as pd

from src.async_url_scraper import AsyncScraper
from src.config import HEADERS, BASE_URL


def create_filename(filename: str, filetype: str) -> str:
    """Creates a filename of format 'ddmmyyyy_filename.filetype'"""
    assert filetype in ['csv', 'json', 'pkl'], 'Invalid file type'
    date = datetime.now().strftime('%d%m%Y')
    filename = f'{date}_{filename}.{filetype}'
    return filename


def create_filepath(filename: str, filetype: str) -> Path:
    """Creates a filepath for the file in the 'data/raw' directory."""
    data_dir = Path('data/raw/')
    return data_dir / create_filename(filename, filetype)


async def main() -> None:
    csv_filepath = create_filepath('reviews', 'csv')
    json_filepath = create_filepath('reviews', 'json')
    
    scraper = AsyncScraper(BASE_URL, HEADERS)
    urls = await scraper.get_urls()
    await scraper.close()

    # TODO: async scrape urls then async scrape reviews from those urls

    print(len(urls))
    print(list(urls)[:5])
    print('Scraping complete. Writing data to file.')


if __name__ == '__main__':
    asyncio.run(main())
