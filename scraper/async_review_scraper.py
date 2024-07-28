import logging

import asyncio
import aiohttp

from .async_review_parser import parse_html


async def fetch(
    url: str, session: aiohttp.ClientSession, semaphore: asyncio.Semaphore, retries: int
) -> str:
    async with semaphore:
        for attempt in range(retries):
            try:
                async with session.get(
                    url, timeout=aiohttp.ClientTimeout(total=20)
                ) as response:
                    if response.status != 200:
                        await asyncio.sleep(5)
                        continue
                    return await response.text()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                await asyncio.sleep(5)
        logging.error(f"Failed to fetch {url} after {retries} attempts.")
        return None


async def scrape_review(
    url: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    retries: int = 10,
) -> str:
    review_page = await fetch(url, session, semaphore, retries=retries)
    if review_page:
        data = await parse_html(review_page)
        data["url"] = url
        return data or "No data found"
    else:
        pass


"""
Just for reference.

async def main():
    semaphore = asyncio.Semaphore(20)
    async with aiohttp.ClientSession(headers=config.HEADERS) as session:
        tasks = [scrape_review(url, session, semaphore) for url in review_urls[0:300]]
        results = []
        for f in tqdm(asyncio.as_completed(tasks), total=len(tasks)):
            result = await f
            results.append(result)
    return results
"""
