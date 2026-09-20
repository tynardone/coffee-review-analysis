"""Scrape a single coffee review page into structured data.

:func:`scrape_review` fetches a review URL through the shared retrying
:func:`coffee.fetch.fetch`, parses the HTML off the event loop, and returns a
dict of the review's fields tagged with its source URL. A page that cannot be
fetched or parsed yields ``None`` rather than raising, so that one malformed
page does not abort a full scrape.
"""

import asyncio
import logging

import aiohttp

from coffee.fetch import fetch
from coffee.parser import parse_html

__all__ = [
    "scrape_review",
]


async def scrape_review(
    url: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    retries: int = 5,
) -> dict | None:
    review_page = await fetch(url, session, semaphore, retries=retries)
    if review_page is None:
        return None
    try:
        # Parse off the event loop so CPU-bound parsing overlaps network I/O.
        data = await asyncio.to_thread(parse_html, review_page)
    except Exception:
        # The caller awaits these one at a time, so an exception escaping here
        # would abort the whole run and write nothing. Returning None puts a
        # parse failure on the same footing as a fetch failure, which the
        # caller already counts and reports.
        logging.exception("Failed to parse %s", url)
        return None
    data["url"] = url
    return data
