"""Fetches historical exchange rates from the OpenExchangeRates API.
For free tier users there's a monthly allowance of 1000 requests."""

import json
from pathlib import Path
from datetime import date

import requests
import pandas as pd
from tqdm import tqdm

from config import OpenExConfig

# Project directories
data_dir: Path = Path(OpenExConfig.BASEDIR) / "data"
dates_csv_path: Path = data_dir / "raw/25072024_reviews.csv"
output_json_path: Path = data_dir / "external/openex_exchange_rates.json"


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
        response = requests.get(
            url,
            headers=OpenExConfig.HEADERS,
            params=params,
            timeout=OpenExConfig.TIMEOUT,
        )
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
        )
    return exchange_rates


def save_exchange_rates(exchange_rates: dict[str, dict[str, float]], output_path: Path):
    """Save exchange rates to a JSON file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(exchange_rates, f)


def main():
    params = {"app_id": OpenExConfig.OPENEXCHANGERATES_API_ID}
    dates = load_date_list(dates_csv_path)
    exchange_rates = fetch_exchange_rates(
        dates, OpenExConfig.API_URL, params, OpenExConfig.TIMEOUT
    )
    save_exchange_rates(exchange_rates, output_json_path)


if __name__ == "__main__":
    main()
