import logging
from pathlib import Path
import pickle

import asyncio
import aiohttp
from tqdm.asyncio import tqdm

from async_review_parser import parse_html


async def fetch(url: str, session: aiohttp.ClientSession,
                semaphore: asyncio.Semaphore, retries: int) -> str:
    async with semaphore:
        for attempt in range(retries):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        logging.warning(
                            f"Failed to fetch {url} with status {response.status}. Retrying...")
                        await asyncio.sleep(1)
                        continue
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logging.error(f"Error fetching {url}: {e}. Retrying...")
                await asyncio.sleep(1)
        logging.error(f"Failed to fetch {url} after {retries} attempts.")
        return None


async def scrape_review(url: str, session: aiohttp.ClientSession,
                        semaphore: asyncio.Semaphore, retries: int = 3) -> str:
    review_page = await fetch(url, session, semaphore, retries=retries)
    if review_page:
        data = await parse_html(review_page)
        data['url'] = url
        return data or "No data found"
    return "Failed to fetch page"


async def load_urls() -> set[str]:
    with open('urls.pkl', 'rb') as f:
        urls = pickle.load(f)
    return urls

import logging
from pathlib import Path
import pickle

import asyncio
import aiohttp
from tqdm.asyncio import tqdm

from async_review_parser import parse_html


async def fetch(url: str, session: aiohttp.ClientSession,
                semaphore: asyncio.Semaphore, retries: int) -> str:
    async with semaphore:
        for attempt in range(retries):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status != 200:
                        logging.warning(
                            f"Failed to fetch {url} with status {response.status}. Retrying...")
                        await asyncio.sleep(1)
                        continue
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logging.error(f"Error fetching {url}: {e}. Retrying...")
                await asyncio.sleep(1)
        logging.error(f"Failed to fetch {url} after {retries} attempts.")
        return None


async def scrape_review(url: str, session: aiohttp.ClientSession,
                        semaphore: asyncio.Semaphore, retries: int = 3) -> str:
    review_page = await fetch(url, session, semaphore, retries=retries)
    if review_page:
        data = await parse_html(review_page)
        data['url'] = url
        return data or "No data found"
    return "Failed to fetch page"


async def load_urls() -> set[str]:
    with open('urls.pkl', 'rb') as f:
        urls = pickle.load(f)
    return urls



if __name__ == "__main__":
    asyncio.run(main())

git