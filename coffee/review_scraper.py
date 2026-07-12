"""Scrape a single coffee review page into structured data.

:func:`scrape_review` fetches a review URL through the shared retrying
:func:`coffee.fetch.fetch`, parses the HTML off the event loop, and returns a
dict of the review's fields tagged with its source URL (or ``None`` if the page
could not be fetched).
"""

import asyncio

import aiohttp

from coffee.fetch import fetch
from coffee.parser import parse_html


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
