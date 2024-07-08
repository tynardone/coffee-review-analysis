import logging
from urllib.parse import urljoin

import time
import asyncio
import aiohttp
from bs4 import BeautifulSoup

import config


class AsyncScraper:
    """
    Asynchronous web scraper to fetch review links from
    Coffeereview.com.
    """
    def __init__(self):
        self.base_url = 'https://www.coffeereview.com/review/'
        self.session = aiohttp.ClientSession()
        self.headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}

    async def fetch(self, url):
        async with self.session.get(url, headers=self.headers) as response:
            try:
                response.raise_for_status()
                return await response.text()
            except aiohttp.ClientResponseError as e:
                logging.error(f'Error fetching {url}: {e}')
                return None

    async def get_urls(self, url: str, visited=None) -> set[str]:
        if visited is None:
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

    scraper = AsyncScraper()
    review_links = await scraper.get_urls(url=config.BASE_URL)
    print(f"Total review links found: {len(review_links)}")

    await scraper.close()

    end_time = time.time()
    elapsed_time = end_time - start_time
    print(f"Elapsed time: {elapsed_time:.2f} seconds")

if __name__ == "__main__":
    asyncio.run(main())
