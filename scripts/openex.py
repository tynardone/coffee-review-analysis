"""Fetches historical exchange rates from the OpenExchangeRates API.
For free tier users there's a monthly allowance of 1000 requests."""

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

# Configuration
API_URL: str = "https://openexchangerates.org/api/historical/"
HEADERS: dict = {
    "accept": "application/json",
    "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
}
DATA_IN: str = "raw/25072024_reviews.csv"
DATA_OUT: str = "external/openex_exchange_rates.json"
TIMEOUT: int = 10

# Project directories
data_dir: Path = Path(__file__).resolve().parent.parent / "data"
dates_csv_path: Path = data_dir / DATA_IN
output_json_path: Path = data_dir / DATA_OUT


def load_api_id() -> str:
    """Loads the OpenExchangeRates API ID from the environment"""
    api_id: str = os.getenv("OPENEXCHANGERATES_API_ID")
    if not api_id:
        raise ValueError("API ID not found.")
    return api_id


def load_date_list(file_path: Path) -> list[date]:
    """Loads scraped data and returns a list of unique dates.
    Filepath should point to recent scraped data."""
    if file_path.suffix not in (".csv", ".json"):
        raise ValueError("Invalid file format. Only CSV and JSON files are supported.")
    if not file_path.exists():
        raise FileNotFoundError(f"{file_path} does not exist.")

    if file_path.suffix == ".json":
        df = pd.read_json(file_path)
    if file_path.suffix == ".csv":
        df = pd.read_csv(file_path)

    # Convert review date to datetime and filter dates from 1999 onwards,
    # this is the earliest date available in the OpenExchangeRates API
    dates = (
        df.assign(review_date=pd.to_datetime(df["review date"]))
        .query('review_date >= "1999-01-01"')
        .sort_values("review_date")
    )
    return dates["review_date"].dt.date.unique().tolist()


def fetch_rate_for_date(date: str, api_url: str, params: dict) -> dict:
    """Fetches historical exchange rates for a given date."""
    url = f"{api_url}{date}.json"
    try:
        response = requests.get(url, headers=HEADERS, params=params, timeout=TIMEOUT)
        response.raise_for_status()
        return response.json().get("rates", {})
    except requests.RequestException as e:
        print(f"Error fetching data for date {date}: {e}")
        return {}


def fetch_exchange_rates(
    dates: list[date],
    api_url: str,
    params: dict[str, str],
    timeout: int,
) -> dict[str, dict[str, float]]:
    """Fetch exchange rates for a list of dates."""
    exchange_rates = {}
    for d in tqdm(dates, desc="Fetching exchange rates..."):
        exchange_rates[str(d)] = fetch_rate_for_date(
            date=str(d),
            api_url=api_url,
            params=params,
            timeout=timeout,
        )
    return exchange_rates


def save_exchange_rates(exchange_rates: dict[str, dict[str, float]], output_path: Path):
    """Save exchange rates to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(exchange_rates, f)


def main():
    params = {"app_id": load_api_id()}
    dates = load_date_list(dates_csv_path)
    exchange_rates = fetch_exchange_rates(dates, API_URL, params, TIMEOUT)
    save_exchange_rates(exchange_rates, output_json_path)


if __name__ == "__main__":
    main()
