"""Module to discover coffee review URLs asynchronously."""

import asyncio
import logging
from urllib.parse import urljoin

import aiohttp
from bs4 import BeautifulSoup

from coffee.fetch import fetch


def _extract_links(html: str, base_url: str) -> tuple[set[str], set[str]]:
    """Return (pagination_links, review_links) found on a listing page."""
    soup = BeautifulSoup(html, "lxml")
    page_links: set[str] = set()
    review_links: set[str] = set()
    for a_tag in soup.find_all("a", href=True):
        # bs4 types a tag attribute as str | AttributeValueList; anchor hrefs
        # are single-valued, so narrow to str (and skip anything unexpected).
        href = a_tag.get("href")
        if not isinstance(href, str):
            continue
        full_url = urljoin(base_url, href)
        if "/review/page/" in href:
            page_links.add(full_url)
        elif (
            "/review/" in href
            and not href.endswith("/page")
            and full_url != base_url
        ):
            review_links.add(full_url)
    return page_links, review_links


async def get_urls(
    base_url: str,
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
) -> set[str]:
    """Crawl the paginated review listings and return every review URL.

    Breadth-first over listing pages: each round fetches the current frontier
    (bounded and retrying via the shared fetch), collects review links, and
    seeds the next round with newly seen pagination links. An explicit visited
    set replaces the previous recursion's shared mutable state.
    """
    logging.info("Discovering review URLs from %s", base_url)
    visited_pages: set[str] = {base_url}
    frontier: set[str] = {base_url}
    review_links: set[str] = set()

    while frontier:
        htmls = await asyncio.gather(
            *(fetch(page, session, semaphore) for page in frontier)
        )
        next_frontier: set[str] = set()
        for html in htmls:
            if not html:
                continue
            page_links, reviews = _extract_links(html, base_url)
            review_links |= reviews
            next_frontier |= page_links - visited_pages
        visited_pages |= next_frontier
        frontier = next_frontier

    logging.info("Discovered %d review URLs", len(review_links))
    return review_links
