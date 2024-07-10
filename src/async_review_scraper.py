import logging
from pathlib import Path
import pickle

import asyncio
import aiohttp
from tqdm.asyncio import tqdm

from .async_review_parser import parse_html

async def fetch(url: str, session: aiohttp.ClientSession,
                semaphore: asyncio.Semaphore, retries: int = 10) -> str:
    async with semaphore:
        for attempt in range(retries):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as response:
                    if response.status != 200:
                        await asyncio.sleep(5)
                        continue
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                await asyncio.sleep(5)
        logging.error(f"Failed to fetch {url} after {retries} attempts.")
        return None


async def scrape_review(url: str, session: aiohttp.ClientSession,
                        semaphore: asyncio.Semaphore, retries: int = 3) -> str:
    review_page = await fetch(url, session, semaphore, retries=retries)
    if review_page:
        data = await parse_html(review_page)
        data['url'] = url
        return data or "No data found"
    else:
        pass


async def scrape_reviews(urls: list[str], session: aiohttp.ClientSession,
                         semaphore: asyncio.Semaphore) -> list[dict]:
    all_data = []
    for url in urls:
        data = await scrape_review(url)
        data['url'] = url
        all_data.append(data)
    return all_data
        
                         
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
