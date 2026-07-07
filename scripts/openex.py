"""Fetch historical exchange rates from the OpenExchangeRates API.

Reads the unique review dates from a scraped reviews file and downloads the
historical rates for each date, writing them to JSON. Free-tier accounts are
limited to 1000 requests per month.
"""

import argparse
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

from coffee.config import OpenExConfig

logger = logging.getLogger(__name__)

DEFAULT_INPUT = OpenExConfig.DATA_DIR / "raw" / "25072024_reviews.csv"
DEFAULT_OUTPUT = OpenExConfig.DATA_DIR / "external" / "openex_exchange_rates.json"

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
        readers[path.suffix](path)["review date"], format="%B %Y"
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
    session.headers.update(OpenExConfig.HEADERS)
    return session


def fetch_rate(session: requests.Session, day: date, app_id: str) -> dict[str, float]:
    """Fetch rates for a single date; return an empty dict on failure."""
    url = f"{OpenExConfig.API_URL}{day}.json"
    try:
        response = session.get(
            url, params={"app_id": app_id}, timeout=OpenExConfig.TIMEOUT
        )
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "-i",
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help="Scraped reviews file (.csv or .json).",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Destination JSON file for exchange rates.",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    args = parse_args()

    app_id = OpenExConfig.OPENEXCHANGERATES_API_ID
    if not app_id:
        raise SystemExit("OPENEXCHANGERATES_API_ID is not set (add it to your .env).")

    dates = load_review_dates(args.input)
    logger.info("Fetching rates for %d unique dates", len(dates))
    rates = fetch_rates(dates, app_id)

    failures = sum(1 for r in rates.values() if not r)
    if failures:
        logger.warning("%d/%d dates returned no rates", failures, len(dates))

    save_rates(rates, args.output)
    logger.info("Wrote exchange rates to %s", args.output)


if __name__ == "__main__":
    main()
