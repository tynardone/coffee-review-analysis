"""
ASYNC ROAST REVIEW SCRAPER

This script loads a list of roast review URLs from data/roast-urls.pkl and scrapes the
review data from each page. The data is saved to data/raw-roast-reviews.json
for further processing.
"""
import asyncio
import pickle
import json
import argparse
from pathlib import Path
from time import perf_counter
import logging

import pandas as pd
from requests_html import AsyncHTMLSession
from tqdm import tqdm
from bs4 import BeautifulSoup

from htmlparser import parse_html

logging.basicConfig(
    level=logging.ERROR,
    format='%(asctime)s - %(levelname)s - %(message)s')

USER_AGENT = 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36\
             (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
FEATURE_LIST = ['Roaster Location', 'Coffee Origin', 'Roast Level', 'Aroma',
                'Acidity/Structure', 'Acidity', 'Body', 'Flavor', 'Aftertaste',
                'Agtron', 'Blind Assessment', 'Notes', 'Bottom Line',
                'Est. Price']

DATA_INPUT = Path('data/raw/roast_urls.pkl')
DATA_OUTPUT = Path('data/raw/raw_roasts_reviews.json')


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description="URL file.")
    parser.add_argument("filename", type=str, help="Path to the JSON file")
    parser.add_argument("-o", "--output", type=str, help="Output directory")
    return parser.parse_args()


async def fetch_roast_review(
        session: AsyncHTMLSession, url: str, progress: tqdm) -> dict:

    r = await session.get(url)
    if r.status_code in (429, 504):
        await asyncio.sleep(3)  # Adjust the delay time (in seconds) as needed
        return await fetch_roast_review(session, url, progress)

    soup = BeautifulSoup(r.text, 'html.parser')
    div_content = soup.find('div', class_='entry-content')
    if div_content:
        div_content = div_content.prettify()
        data = parse_html(div_content)
        if data:
            for item in data.items():
                if item[1] is None:
                    logging.warning("Missing data for %s in %s", item[0], url)
            data['url'] = url
            progress.update()
            return data
    progress.update()
    return None


async def gather_tasks(urls: list[str], progress: tqdm):
    session = AsyncHTMLSession()
    tasks = [fetch_roast_review(session, url, progress) for url in urls]
    return await asyncio.gather(*tasks)


def main():
    args = parse_args()
    input_file = Path(args.filename)
    output_file = Path(args.output) if args.output else DATA_OUTPUT

    with open(input_file, 'rb') as f:
        if input_file.suffix == '.pkl':
            urls = pickle.load(f)
        else:
            urls = pd.read_csv(f).url.tolist()

    start = perf_counter()
    progress_bar = tqdm(total=len(urls), desc="Scraping roast pages")
    results = asyncio.run(gather_tasks(urls, progress=progress_bar))
    progress_bar.close()
    end = perf_counter()

    print(f"Ran in {end - start:0.4f} seconds")

    with open(output_file, 'w', encoding="utf-8") as fout:
        json.dump(results, fout)


if __name__ == '__main__':
    main()
