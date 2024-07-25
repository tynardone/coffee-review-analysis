"""Fetches historical exchange rates from the OpenExchangeRates API."""

import json
import os
from pathlib import Path
from datetime import date

import requests
import pandas as pd
from dotenv import load_dotenv
from tqdm import tqdm

# Load environment variables from .env file
load_dotenv()

# URL for the OpenExchangeRates API
API_URL = "https://openexchangerates.org/api/historical/"
HEADERS = {
    "accept": "application/json",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}

# Define base paths
project_root = Path(__file__).resolve().parent.parent

# Define directory paths from base path
dates_csv_path = project_root / "data" / "processed" / "review_dates.csv"
output_json_path = project_root / "data" / "external" / "openex_exchange_rates.json"


def load_api_id() -> str:
    """Loads the OpenExchangeRates API ID from the environment"""
    api_id = os.getenv("OPENEXCHANGERATES_API_ID")
    if not api_id:
        raise ValueError("API ID not found.")
    return api_id


def load_date_list(file_path: Path) -> list[date]:
    """Loads a list of dates from a CSV file."""
    if not file_path.exists():
        raise FileNotFoundError(f"{file_path} does not exist.")

    dates = pd.read_csv(file_path)
    dates = dates.assign(review_date=pd.to_datetime(dates.review_date)).query(
        'review_date >= "1999-01-01"'
    )
    return dates.review_date.dt.date.tolist()


def fetch_rate_for_date(date: str, api_url: str, headers: dict, params: dict) -> dict:
    """Fetches historical exchange rates for a given date."""
    url = f"{api_url}{date}.json"
    response = requests.get(url, headers=headers, params=params, timeout=10)
    response.raise_for_status()
    return response.json().get("rates", {})


def main():
    params = {"app_id": load_api_id()}

    dates = load_date_list(dates_csv_path)
    exchange_rates = {}

    for d in tqdm(dates, desc="Fetching exchange rates..."):
        exchange_rates[str(d)] = fetch_rate_for_date(
            date=d, api_url=API_URL, headers=HEADERS, params=params
        )

    output_json_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_json_path, "w", encoding="utf-8") as f:
        json.dump(exchange_rates, f)


if __name__ == "__main__":
    main()
