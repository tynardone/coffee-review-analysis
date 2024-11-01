"""Module to scrape coffee review URLs asynchronously."""

import logging
from urllib.parse import urljoin
from typing import Coroutine, Any

import asyncio
import aiohttp
from bs4 import BeautifulSoup


async def fetch(url: str, session: aiohttp.ClientSession):
    try:
        response = await session.get(url)
        response.raise_for_status()
        return await response.text()
    except aiohttp.ClientResponseError as e:
        logging.error(f"Error fetching {url}: {e}")
        return None


async def get_urls(
    base_url: str,
    session: aiohttp.ClientSession,
    url: str = "",
    visited: set | None = None,
) -> set[str]:
    if visited is None:
        url = base_url
        visited = set()
        logging.info(f"Starting to fetch links from {url}")

    review_links: set = set()

    try:
        html = await fetch(url, session)
        if html:
            soup = BeautifulSoup(html, "html.parser")

            tasks: list[Coroutine[Any, Any, set[str]]] = []
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                full_url = urljoin(base_url, href)
                if full_url in visited:
                    continue
                if "/review/page/" in href:
                    visited.add(full_url)
                    tasks.append(
                        get_urls(
                            base_url=base_url,
                            session=session,
                            url=full_url,
                            visited=visited,
                        )
                    )
                elif (
                    "/review/" in href
                    and not href.endswith("/page")
                    and href != base_url
                ):
                    review_links.add(full_url)

            results = await asyncio.gather(*tasks)
            for result in results:
                review_links.update(result)

    except aiohttp.ClientError as e:
        logging.error(f"Error fetching {url}: {e}")

    return review_links
