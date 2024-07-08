"""Fetches historical exchange rates from the OpenExchangeRates API."""

import json
import os
import logging
from pathlib import Path

import requests
import pandas as pd
from dotenv import load_dotenv
from rich.progress import Progress

# Load environment variables from .env file
load_dotenv()

# Set logging level
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Define base paths
project_root = Path(__file__).resolve().parent.parent

# Define directory paths from base path
DATES_CSV_PATH = project_root / 'data' / 'processed' / 'review_dates.csv'
OUTPUT_JSON_PATH = project_root / 'data' / 'external' / 'openex_exchange_rates.json'

# Url for the OpenExchangeRates API
API_URL = 'https://openexchangerates.org/api/historical/'

def load_api_id() -> str:
    """Loads the OpenExchangeRates API ID from the environment.

    Returns:
        str: OpenExchangeRates API ID
    """
    api_id = os.getenv('OPENEXCHANGERATES_API_ID')
    if not api_id:
        logging.error("API ID not found. Please set OPENEXCHANGERATES_API_ID in your environment.")
        raise ValueError("API ID not found.")
    return api_id

def load_date_list(file_path: str) -> list[str]:
    """Loads a list of dates from a CSV file.

    Args:
        file_path (str): Path to the CSV file containing dates

    Returns:
        list[str]: List of dates
    """
    dates = pd.read_csv(file_path)
    dates = (dates.assign(review_date=pd.to_datetime(dates.review_date))
             .query('review_date >= "1999-01-01"'))
    return dates.review_date.dt.date.tolist()

def fetch_rate_for_date(date: str, api_url: str, headers: dict,
                        params: dict) -> dict[str, list[float]]:
    """Fetches historical exchange rates for a given date.

    Args:
        date (str): YYYY-MM-DD formatted date string
        api_url (str): Base URL for the API
        headers (dict): Request headers
        params (dict): Request parameters

    Returns:
        dict[str, list[float]]: List of exchange rates for the given date
    """
    url = f"{api_url}{date}.json"
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
    except requests.exceptions.HTTPError as errh:
        logging.error("HTTP Error: %s", errh)
    return response.json()['rates']

def main():
    """Fetch and save exchange rates for provided dates."""
    headers = {"accept": "application/json",
               "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) \
                   AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
               }
    params = {'app_id': load_api_id()}

    dates = load_date_list(DATES_CSV_PATH)
    exchange_rates = {}

    with Progress() as progress:
        task = progress.add_task("[green]Fetching exchange rates...", total=len(dates))                        
        for date in dates:
            exchange_rates[str(date)] = fetch_rate_for_date(date,
                                                            api_url=API_URL,
                                                            headers=headers,
                                                            params=params)
            progress.advance(task)

    with open(OUTPUT_JSON_PATH, 'w', encoding='utf-8') as f:
        json.dump(exchange_rates, f)
    logging.info("Exchange rates fetched and saved successfully.")

if __name__ == "__main__":
    main()
