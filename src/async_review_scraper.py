import logging
from pathlib import Path
import pickle

import asyncio
import aiohttp
from tqdm.asyncio import tqdm

import config

from async_review_parser import parse_html


async def fetch(url: str, session: aiohttp.ClientSession,
                semaphore: asyncio.Semaphore, retries: int = 3) -> str:
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
                        semaphore: asyncio.Semaphore) -> str:
    review_page = await fetch(url, session, semaphore)
    if review_page:
        data = await parse_html(review_page)
        return data or "No data found"
    return "Failed to fetch page"


async def main():
    semaphore = asyncio.Semaphore(20)
    async with aiohttp.ClientSession(headers=config.HEADERS) as session:
        tasks = [scrape_review(url, session, semaphore)
                 for url in review_urls[0:300]]
        results = []
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
            result = await f
            results.append(result)
    return results

if __name__ == '__main__':
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s')
    root_dir = Path(__file__).parent.parent
    data_dir = root_dir / 'data' / 'raw'

    # Find all pickle files in the data directory
    files = [file for file in data_dir.iterdir() if file.suffix == '.pkl']
    file = files[0]

    # Load review URLs from the pickle file
    with open(file, 'rb') as file:
        review_urls = pickle.load(file)

    print(f"Loaded {len(review_urls)} review URLs.")

    review_data = asyncio.run(main())
    print(review_data)
