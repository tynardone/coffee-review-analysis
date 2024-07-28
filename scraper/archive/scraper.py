import logging
import time
from pathlib import Path
from typing import Optional, Any, Iterable
from urllib.parse import urljoin
import requests
from tqdm import tqdm
from bs4 import BeautifulSoup
from . import config
from .review_parser import parse_html
from tqdm import tqdm

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)


def save_html(html: str, url: str):
    root_dir = Path(__file__).parent.parent
    html_dir = root_dir / "src" / "test_html"
    url_part = url.split("/")[-2].replace("-", "_")
    with open(html_dir / f"review_{url_part}.html", "w") as f:
        f.write(html)


class ReviewScraper:
    """
    A class to scrape coffee reviews from coffeereview.com.

    This class provides methods to fetch review pages from specified URLs,
    parse the HTML content using BeautifulSoup, and extract review data into structured formats.

    Attributes:
        base_url (str): The base URL of the website to scrape. Defaults to 'https://www.coffeereview.com/review/'.
        headers (dict): A dictionary of HTTP headers to use for requests. This can be used to simulate a browser visit.
        session (requests.Session): An HTTP session to manage connections. This is used to maintain session state across requests.

    Methods:
        __init__(self, base_url: str, headers: dict = None):
            Initializes the ReviewScraper with a base URL and optional HTTP headers.

        __enter__(self):
            Context manager entry method to initialize the requests session.

        __exit__(self, exc_type, exc_value, traceback):
            Context manager exit method to close the requests session.

        fetch_html(self, url: str) -> BeautifulSoup:
            Fetches the HTML content of a page by URL and returns a BeautifulSoup object for parsing.

        get_review_urls(self, url: str = None, visited: set = None) -> set[str]:
            Recursively fetches and returns a set of URLs for review pages, starting from the base URL or the given URL.

        parse_review_page(self, soup: BeautifulSoup) -> dict:
            Parses a single review page's BeautifulSoup object and extracts relevant review data into a dictionary.

        parse_reviews(self, reviews: dict[str, BeautifulSoup]) -> list[dict]:
            Parses multiple review pages given a dictionary of URLs and their corresponding BeautifulSoup objects, returning a list of review data dictionaries.

        fetch_and_parse_review(self, urls: Iterable[str]) -> list[dict]:
            Fetches and parses multiple review pages given an iterable of URLs, returning a list of dictionaries containing the extracted review data.
    """

    def __init__(self, base_url: str, headers: dict = None):
        self.base_url = base_url
        self.headers = headers
        self.session = None

    def __enter__(self):
        self.session = requests.Session()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.session.close()

    def fetch_html(self, url: str) -> BeautifulSoup:
        """Fetch the HTML content of a page and return a BeautifulSoup object.

        Args:
            url (str): The URL of the page to fetch.

        Returns:
            BeautifulSoup: A BeautifulSoup object of the fetched page.
        """
        response = self.session.get(url, headers=self.headers)
        response.raise_for_status()
        return BeautifulSoup(response.text, "html.parser")

    def get_review_urls(
        self, url: str = None, visited: set = None, limit: int = None
    ) -> set[str]:
        """Fetch a page and extract all relevant review links, recursively
        fetching additional review pages.

        Args:
            url (str): The URL of the page to fetch.
            visited (set[str], optional): A set of visited URLs to avoid
            duplicate processing.s Defaults to None.

        Returns:
            set[str]: A set of URLs of review pages.
        """
        if url is None:
            url = self.base_url
        if visited is None:
            visited = set()
            logging.info(f"Starting to fetch links from {url}")

        review_links = set()
        logging.info("Fetching links from %s", url)
        try:
            soup = self.fetch_html(url)

            # Find links of the form '/reviews/'
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"]
                full_url = urljoin(self.base_url, href)

                if full_url in visited:
                    continue
                # if a link to a pagination page fetch links from it
                if "/review/page/" in href:
                    # Recursively fetch links from pagination pages
                    visited.add(full_url)
                    review_links.update(self.get_review_urls(full_url, visited))

                # if a link to a review page add it to the set
                elif (
                    "/review/" in href
                    and not href.endswith("/page")
                    and href != self.base_url
                ):
                    review_links.add(full_url)
        except requests.RequestException as e:
            print(f"Error fetching page: {e}")
            return None
        return review_links

    def fetch_and_parse_review(self, urls: Iterable[str]) -> Iterable[dict]:
        """Fetch and parse multiple review pages and return a list of extracted data."""
        review_data = []
        for url in tqdm(urls, desc="Fetching reviews"):
            try:
                soup = self.fetch_html(url)
                data.update(parse_review(soup))
                data["url"] = url
                review_data.append(data)
            except requests.RequestException as e:
                data["url"] = url
                review_data.append(data)
                print(f"Error fetching page: {e}")
        return review_data


def main():
    start_time = time.time()

    with ReviewScraper(base_url=config.BASE_URL, headers=config.HEADERS) as scraper:
        review_urls = scraper.get_review_urls(limit=10)
        logging.info("Total review links found: %s", len(review_urls))
        review_data = scraper.fetch_and_parse_review(review_urls)
    end_time = time.time()

    elapsed_time = end_time - start_time
    logging.info("Time elapsed: %.2f seconds", elapsed_time)

    print(review_data)


if __name__ == "__main__":
    main()
