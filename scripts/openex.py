"""Fetches historical exchange rates from the OpenExchangeRates API.
For free tier users there's a monthly allowance of 1000 requests."""

import json
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from tqdm import tqdm

from src.config import OpenExConfig

# Project directories
data_dir: Path = Path(OpenExConfig.BASEDIR) / "data"
dates_csv_path: Path = data_dir / "raw/25072024_reviews.csv"
output_json_path: Path = data_dir / "external/openex_exchange_rates.json"


def load_date_list(file_path: Path) -> list[date]:
    """
    Load a list of unique review dates from a CSV or JSON file.

    Args:
        file_path (Path): Path to the file containing scraped data.

    Returns:
        list[date]: List of unique dates from 1999 onwards.

    Raises:
        ValueError: If the file format is not CSV or JSON.
        FileNotFoundError: If the file does not exist.
    """
    if file_path.suffix not in (".csv", ".json"):
        raise ValueError("Invalid file format. Only CSV and JSON files are supported.")
    if not file_path.exists():
        raise FileNotFoundError(f"{file_path} does not exist.")

    if file_path.suffix == ".json":
        df = pd.read_json(file_path)
    if file_path.suffix == ".csv":
        df = pd.read_csv(file_path)

    # Filter dates from 1999 onwards, this is the earliest date available
    # in the OpenExchangeRates API
    dates = (
        df.assign(review_date=pd.to_datetime(df["review date"]))
        .query('review_date >= "1999-01-01"')
        .sort_values("review_date")
    )
    return dates["review_date"].dt.date.unique().tolist()


def fetch_rate_for_date(date: str, api_url: str, params: dict) -> dict:
    """
    Fetch historical exchange rates for a specific date.

    Args:
        date (str): Date in 'YYYY-MM-DD' format.
        api_url (str): Base API URL for fetching rates.
        params (dict): Query parameters for the API request.

    Returns:
        dict: Dictionary of exchange rates for the given date.
    """
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
) -> dict[str, dict[str, float]]:
    """
    Fetch exchange rates for a list of dates.

    Args:
        dates (list[date]): List of dates to fetch exchange rates for.
        api_url (str): Base API URL for fetching rates.
        params (dict[str, str]): Query parameters for the API request.
        timeout (int): Request timeout duration in seconds.

    Returns:
        dict[str, dict[str, float]]: Dictionary mapping dates to their exchange rates.
    """
    exchange_rates = {}
    for d in tqdm(dates, desc="Fetching exchange rates..."):
        exchange_rates[str(d)] = fetch_rate_for_date(
            date=str(d),
            api_url=api_url,
            params=params,
        )
    return exchange_rates


def save_exchange_rates(exchange_rates: dict[str, dict[str, float]], output_path: Path):
    """
    Save exchange rates to a JSON file.

    Args:
        exchange_rates (dict[str, dict[str, float]]): Dictionary of exchange rates.
        output_path (Path): Path to save the JSON file.
    """
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(exchange_rates, f)


def main():
    """Run script. Load dates, fetch exchange rates, and save to file."""
    params = {"app_id": OpenExConfig.OPENEXCHANGERATES_API_ID}
    dates = load_date_list(dates_csv_path)
    exchange_rates = fetch_exchange_rates(
        dates, OpenExConfig.API_URL, params, OpenExConfig.TIMEOUT
    )
    save_exchange_rates(exchange_rates, output_json_path)


if __name__ == "__main__":
    main()
