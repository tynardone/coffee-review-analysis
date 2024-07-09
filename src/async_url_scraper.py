import logging
from urllib.parse import urljoin

import time
import asyncio
import aiohttp
from bs4 import BeautifulSoup

from src import config


class AsyncScraper():
    """
    Asynchronous web scraper to fetch review links from
    Coffeereview.com.
    """

    def __init__(self, url: str, headers: dict = None):
        self.base_url = url
        self.session = aiohttp.ClientSession()
        self.headers = headers

    async def fetch(self, url):
        async with self.session.get(url, headers=self.headers) as response:
            try:
                response.raise_for_status()
                return await response.text()
            except aiohttp.ClientResponseError as e:
                logging.error(f'Error fetching {url}: {e}')
                return None

    async def get_urls(self, url: str = None, visited: set = None) -> set[str]:
        if visited is None:
            url = self.base_url
            visited = set()
            logging.info(f'Starting to fetch links from {url}')

        review_links = set()

        try:
            html = await self.fetch(url)
            if html:
                soup = BeautifulSoup(html, 'html.parser')

                tasks = []
                for a_tag in soup.find_all('a', href=True):
                    href = a_tag['href']
                    full_url = urljoin(self.base_url, href)
                    if full_url in visited:
                        continue
                    if '/review/page/' in href:
                        visited.add(full_url)
                        tasks.append(self.get_urls(full_url, visited))
                    elif '/review/' in href and not href.endswith('/page') and href != self.base_url:
                        review_links.add(full_url)

                results = await asyncio.gather(*tasks)
                for result in results:
                    review_links.update(result)

        except aiohttp.ClientError as e:
            logging.error(f'Error fetching {url}: {e}')

        return review_links

    async def close(self):
        await self.session.close()


async def main():
    start_time = time.time()

    scraper = AsyncScraper(url=config.BASE_URL, headers=config.HEADERS)
    review_links = await scraper.get_urls(url=config.BASE_URL)
    print(f"Total review links found: {len(review_links)}")

    await scraper.close()

    end_time = time.time()
    elapsed_time = end_time - start_time
    print(f"Elapsed time: {elapsed_time:.2f} seconds")

if __name__ == "__main__":
    asyncio.run(main())
