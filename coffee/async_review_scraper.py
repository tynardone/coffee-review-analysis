"""Module for scraping coffee review page asynchronously."""

import asyncio

import aiohttp

from coffee.async_parser import parse_html
from coffee.fetch import fetch


async def scrape_review(
    url: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    retries: int = 5,
) -> dict | None:
    review_page = await fetch(url, session, semaphore, retries=retries)
    if review_page is None:
        return None
    # Parse off the event loop so CPU-bound parsing overlaps network I/O.
    data = await asyncio.to_thread(parse_html, review_page)
    data["url"] = url
    return data
