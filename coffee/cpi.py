"""Fetch the BLS consumer price index used to put prices in constant dollars.

Reads the CPI-U series from the BLS public API and merges it into the table at
``data/external/consumer_price_index.csv``. The series is ``CUUR0000SA0``: all
urban consumers, U.S. city average, all items, not seasonally adjusted, which
is what :func:`coffee.prices.cpi_adjust_price` expects.

Runs incrementally, like :mod:`coffee.exchange_rates`. A published CPI figure
for a past month does not change, so only the recent window is requested and
merged into what is held. The v1 API returns the last three years without a
key, which covers any gap short of leaving this unrun for that long.

The stored table keeps the BLS's own wide layout -- a row per year, a column
per month, plus the semiannual averages -- so the file stays readable and
:func:`coffee.prices.load_cpi` needs no change. The API does not publish the
semiannual columns, so they are left as found.
"""

import logging
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from coffee.config import DATA_DIR, HEADERS

__all__ = [
    "BLS_API_URL",
    "BLS_SERIES_ID",
    "DEFAULT_CPI_PATH",
    "MONTH_COLUMNS",
    "Observation",
    "fetch_cpi",
    "fetch_observations",
    "load_cpi_table",
    "merge_observations",
    "save_cpi_table",
]

logger = logging.getLogger(__name__)

DEFAULT_CPI_PATH = DATA_DIR / "external" / "consumer_price_index.csv"

# CPI-U, U.S. city average, all items, not seasonally adjusted.
BLS_SERIES_ID = "CUUR0000SA0"
BLS_API_URL = "https://api.bls.gov/publicAPI/v1/timeseries/data/"
BLS_TIMEOUT = 20

MONTH_COLUMNS = [
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
]  # fmt: skip

# The semiannual averages. Kept so the file keeps its shape, never written:
# the API does not publish them.
HALF_COLUMNS = ["HALF1", "HALF2"]

# (year, month number 1-12, index value exactly as BLS printed it). The value
# stays a string: re-reading the table as floats and writing it back turns
# "208.490" into "208.49", rewriting rows this fetch never touched.
Observation = tuple[int, int, str]


def _observations_from_payload(payload: dict[str, Any]) -> list[Observation]:
    """Pull (year, month, value) rows out of a BLS API response.

    Period ``M13`` is the annual average rather than a month. Letting it
    through would land as a thirteenth month and skew the series, so anything
    outside M01-M12 is dropped.
    """
    if payload.get("status") != "REQUEST_SUCCEEDED":
        messages = "; ".join(payload.get("message") or []) or "no message"
        raise RuntimeError(f"BLS API returned {payload.get('status')}: {messages}")

    series = payload.get("Results", {}).get("series") or []
    if not series:
        raise RuntimeError("BLS API returned no series")

    observations: list[Observation] = []
    for point in series[0].get("data", []):
        period = point.get("period", "")
        if not (period.startswith("M") and period != "M13"):
            continue
        # BLS writes "-" for a month it has not published. October 2025 is one:
        # the index was never produced during that year's lapse in
        # appropriations. Such a month is absent rather than wrong, so it is
        # skipped quietly and simply stays blank in the table.
        if str(point.get("value", "")).strip() in {"", "-"}:
            logger.info(
                "CPI not published for %s-%s; leaving it blank",
                point.get("year"),
                period,
            )
            continue
        try:
            value = str(point["value"]).strip()
            float(value)  # validate; the text itself is what gets stored
            observations.append((int(point["year"]), int(period[1:]), value))
        except (KeyError, ValueError):
            logger.warning("Skipping unreadable CPI point %r", point)
    return sorted(observations)


def fetch_observations(
    start_year: int | None = None, end_year: int | None = None
) -> list[Observation]:
    """Fetch CPI observations from the BLS API.

    With no years, the v1 API returns its default window of the last three
    years, which needs no registration key. Passing a range switches to the
    POST form, which the same unkeyed tier allows for spans of up to ten years.
    """
    if start_year is None and end_year is None:
        response = requests.get(
            f"{BLS_API_URL}{BLS_SERIES_ID}", headers=HEADERS, timeout=BLS_TIMEOUT
        )
    else:
        response = requests.post(
            BLS_API_URL,
            json={
                "seriesid": [BLS_SERIES_ID],
                "startyear": str(start_year),
                "endyear": str(end_year),
            },
            headers={**HEADERS, "Content-Type": "application/json"},
            timeout=BLS_TIMEOUT,
        )
    response.raise_for_status()
    return _observations_from_payload(response.json())


def load_cpi_table(path: Path) -> pd.DataFrame:
    """Read the stored wide table; a missing file means nothing is held."""
    if not path.exists():
        return pd.DataFrame(columns=["Year", *MONTH_COLUMNS, *HALF_COLUMNS])
    # Read every cell as text so that values this fetch does not touch are
    # written back exactly as they were found.
    return pd.read_csv(path, dtype=str).fillna("")


def save_cpi_table(table: pd.DataFrame, path: Path) -> None:
    """Write the wide table back, oldest year first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = table.sort_values("Year", key=lambda c: c.astype(int))
    ordered.to_csv(path, index=False)


def merge_observations(
    held: pd.DataFrame, observations: list[Observation]
) -> pd.DataFrame:
    """Fold observations into the wide table, adding years as needed.

    A published figure is authoritative, so an incoming value replaces a held
    one. Months the API did not return keep whatever they held, which is what
    stops a three-year window from blanking thirty years of history.
    """
    table = held.copy().astype(str)
    for column in ["Year", *MONTH_COLUMNS, *HALF_COLUMNS]:
        if column not in table.columns:
            table[column] = ""
    table = table[["Year", *MONTH_COLUMNS, *HALF_COLUMNS]]

    for year, month, value in observations:
        key = str(year)
        if key not in set(table["Year"]):
            blank = dict.fromkeys(table.columns, "")
            blank["Year"] = key
            table = pd.concat([table, pd.DataFrame([blank])], ignore_index=True)
        table.loc[table["Year"] == key, MONTH_COLUMNS[month - 1]] = value

    return table.sort_values("Year", key=lambda c: c.astype(int)).reset_index(drop=True)


def _coverage(table: pd.DataFrame) -> int:
    """How many month cells in the table carry a value."""
    if table.empty:
        return 0
    filled = (
        table[MONTH_COLUMNS].astype(str).map(lambda v: v.strip() not in {"", "nan"})
    )
    return int(filled.to_numpy().sum())


def fetch_cpi(
    path: Path = DEFAULT_CPI_PATH,
    *,
    start_year: int | None = None,
    end_year: int | None = None,
) -> pd.DataFrame:
    """Bring the CPI table at `path` up to date and return it.

    Refuses to write a table covering fewer months than the one it loaded, on
    the same reasoning as the exchange-rate fetch: the stored file is the only
    copy, and a partial response must not be allowed to shrink it.
    """
    held = load_cpi_table(path)
    observations = fetch_observations(start_year, end_year)
    logger.info("BLS returned %d monthly observation(s)", len(observations))

    merged = merge_observations(held, observations)
    if _coverage(merged) < _coverage(held):
        raise ValueError(
            f"Refusing to write {path}: the result covers "
            f"{_coverage(merged)} month(s) against {_coverage(held)} already "
            "on disk."
        )

    save_cpi_table(merged, path)
    return merged
