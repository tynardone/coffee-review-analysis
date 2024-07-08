"""
ROAST REVIEW URL SCRAPER

This script scrapes the URLs for all individual roast reviews from the review list pages
on coffeereview.com. The URLs are pickled and saved to data/roast-urls.pkl
to be used by async_scrape_roast_reviews.py.
"""
import asyncio
import pickle

from requests_html import AsyncHTMLSession
from tqdm import tqdm

BASE_URL = 'https://www.coffeereview.com/review/page/'
USER_AGENT = ('Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) '
              'AppleWebKit/537.36 (KHTML, like Gecko) '
              'Chrome/114.0.0.0')

TOTAL_PAGES = 388
DATA_OUTPUT = 'data/raw/roast_urls.pkl'

async def scrape_review_list(session: AsyncHTMLSession, url: str, progress: tqdm) -> list[str]:
    """
    Scrapes urls for all individual roast reviews from a single review list page.

    Args:
        session (AsyncHTMLSession): The async HTML session to use for the request.
        url (str): The URL of the review list page to scrape.
        pbar (tqdm): The progress bar to update.

    Returns:
        list[str]: A list of URLs for all individual roast reviews found on the page.
    """
    r = await session.get(url)
    all_links = r.html.links
    filtered_links = [links for links in all_links
                      if '/review/' in links
                      and '/page/' not in links
                      and links != 'https://www.coffeereview.com/review/']
    if r.status_code in (429, 504):
        # You can adjust the delay time (in seconds) as needed
        await asyncio.sleep(3)
        return await scrape_review_list(session, url, progress)
    progress.update()
    return filtered_links


async def gather_tasks(url_list: list[str], progress: tqdm):
    session = AsyncHTMLSession()
    tasks = [scrape_review_list(session, url, progress) for url in url_list]
    return await asyncio.gather(*tasks)

def main():
    """
    Main function to scrape roast review urls.
    """
    urls = [BASE_URL + f"{page_number}/" for page_number in range(1, TOTAL_PAGES)]
    pbar = tqdm(total=len(urls), desc="Scraping roast urls")
    results = asyncio.run(gather_tasks(urls, progress=pbar))
    pbar.close()
    flat_list = [item for sublist in results for item in sublist]
    print(f"Found {len(flat_list)} URLS")

    with open(DATA_OUTPUT, 'wb') as fout:
        pickle.dump(flat_list, fout)

if __name__ == '__main__':
    main()
