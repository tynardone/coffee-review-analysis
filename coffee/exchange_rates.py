"""Fetch historical exchange rates from the OpenExchangeRates API.

Reads the unique review dates from a scraped reviews file and downloads the
historical rates for each date. Free-tier accounts are limited to 1000
requests per month, so callers should avoid re-fetching dates they already
have.
"""

import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

from coffee.config import DATA_DIR, HEADERS, OPENEX_API_URL, OPENEX_TIMEOUT

__all__ = [
    "DEFAULT_OUTPUT",
    "fetch_rate",
    "fetch_rates",
    "load_review_dates",
    "save_rates",
]

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = DATA_DIR / "external" / "openex_exchange_rates.json"

# OpenExchangeRates' historical data begins in 1999.
EARLIEST_DATE = "1999-01-01"


def load_review_dates(path: Path) -> list[date]:
    """Return the sorted, unique review dates (>= 1999) from a scraped file."""
    readers = {".csv": pd.read_csv, ".json": pd.read_json}
    if path.suffix not in readers:
        raise ValueError(f"Unsupported file type {path.suffix!r}; use .csv or .json.")
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")

    # Review dates are stored as "Month Year", e.g. "November 2016".
    review_dates = pd.to_datetime(
        readers[path.suffix](path)["review_date"], format="%B %Y"
    )
    return (
        review_dates[review_dates >= EARLIEST_DATE]
        .dt.date.drop_duplicates()
        .sort_values()
        .tolist()
    )


def _build_session(retries: int = 3) -> requests.Session:
    """Session that reuses connections and retries transient errors."""
    retry = Retry(
        total=retries,
        backoff_factor=1,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=("GET",),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update(HEADERS)
    return session


def fetch_rate(session: requests.Session, day: date, app_id: str) -> dict[str, float]:
    """Fetch rates for a single date; return an empty dict on failure."""
    url = f"{OPENEX_API_URL}{day}.json"
    try:
        response = session.get(url, params={"app_id": app_id}, timeout=OPENEX_TIMEOUT)
        response.raise_for_status()
        return response.json().get("rates", {})
    except requests.RequestException:
        logger.warning("Failed to fetch rates for %s", day, exc_info=True)
        return {}


def fetch_rates(dates: list[date], app_id: str) -> dict[str, dict[str, float]]:
    """Fetch rates for every date, keyed by ISO date string."""
    session = _build_session()
    return {
        str(day): fetch_rate(session, day, app_id)
        for day in tqdm(dates, desc="Fetching exchange rates")
    }


def save_rates(rates: dict[str, dict[str, float]], path: Path) -> None:
    """Write the exchange-rate mapping to a JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(rates, f, indent=2)
