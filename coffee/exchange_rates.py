"""Fetch historical exchange rates from the OpenExchangeRates API.

Reads the unique review months from the cleaned layer and downloads the
historical rate for each one. Those months are what
:func:`coffee.enrich.convert_currency` merges against, so taking them from the
cleaned layer requests exactly the rates that will be used, rather than also
requesting rates for rows that cleaning drops.

The cleaned layer is built from the raw scrape alone, so this reads a file that
already exists rather than one this command is needed to produce. The order is
scrape, clean, fetch rates, enrich. :func:`load_review_dates` also accepts the
raw layer, for fetching rates before a cleaned layer has been built.

Runs incrementally. Rates for a past date do not change, so a date already held
is never re-fetched. This matters because free-tier accounts are limited to
1000 requests per month and the current corpus spans 323 distinct months: an
unconditional run would spend a third of the monthly budget re-downloading
values that cannot have moved.

Two properties protect the stored file, which is the only copy:

* A failed fetch is never persisted. :func:`merge_rates` keeps the held value
  whenever the incoming one is empty, so a rate-limited run cannot replace
  populated dates with blanks.
* Results are checkpointed as they arrive rather than at the end, so a run that
  dies partway keeps the requests it already spent.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry

from coffee.config import DATA_DIR, HEADERS, OPENEX_API_URL, OPENEX_TIMEOUT

__all__ = [
    "DEFAULT_CHECKPOINT_EVERY",
    "DEFAULT_OUTPUT",
    "RateMapping",
    "fetch_rate",
    "fetch_rates",
    "load_rates",
    "load_review_dates",
    "merge_rates",
    "save_rates",
    "unfetched_dates",
]

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = DATA_DIR / "external" / "openex_exchange_rates.json"

# OpenExchangeRates' historical data begins in 1999.
EARLIEST_DATE = "1999-01-01"

# How many fetches to accumulate before writing. Every date would mean hundreds
# of writes of a file approaching a megabyte; the end of the run would mean
# losing everything on a crash.
DEFAULT_CHECKPOINT_EVERY = 25

# ISO date string -> currency code -> rate against USD.
RateMapping = dict[str, dict[str, float]]


def _review_dates(column: pd.Series) -> pd.Series:
    """Parse a ``review_date`` column from either data layer.

    The cleaned layer stores ISO dates, the raw layer the site's own "November
    2016". Both are accepted so that rates can still be fetched from the raw
    scrape before a cleaned layer exists; see the note on ordering above.
    """
    try:
        return pd.to_datetime(column, format="ISO8601")
    except ValueError:
        return pd.to_datetime(column, format="%B %Y")


def load_review_dates(path: Path) -> list[date]:
    """Return the sorted, unique review months (>= 1999) from a reviews file.

    Read from the cleaned layer by default, because the months that need a rate
    are exactly the months the cleaned layer will convert. Reading from the raw
    scrape instead would request rates for rows that cleaning drops.
    """
    readers = {".csv": pd.read_csv, ".json": pd.read_json}
    if path.suffix not in readers:
        raise ValueError(f"Unsupported file type {path.suffix!r}; use .csv or .json.")
    if not path.exists():
        raise FileNotFoundError(f"{path} does not exist.")

    review_dates = _review_dates(readers[path.suffix](path)["review_date"])
    return (
        review_dates[review_dates >= EARLIEST_DATE]
        .dt.date.drop_duplicates()
        .sort_values()
        .tolist()
    )


def load_rates(path: Path) -> RateMapping:
    """Read a saved rate file; a missing one means nothing is held yet."""
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_rates(rates: Mapping[str, Mapping[str, float]], path: Path) -> None:
    """Write the exchange-rate mapping to a JSON file.

    Writes exactly what it is given; the decision about what to keep belongs to
    :func:`merge_rates`. Keys are sorted so that appending a date produces a
    readable diff rather than reordering the file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(rates, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def unfetched_dates(dates: Iterable[date], held: Mapping[str, Mapping]) -> list[date]:
    """Which of `dates` still need fetching, in order.

    A date counts as held only when its entry is non-empty. An entry recorded
    as ``{}`` is a failed attempt rather than a result, so it is retried; the
    alternative would let one rate-limited run poison that date permanently.
    """
    return [day for day in dates if not held.get(str(day))]


def merge_rates(
    held: Mapping[str, Mapping[str, float]],
    fetched: Mapping[str, Mapping[str, float]],
) -> RateMapping:
    """Combine held and newly fetched rates, preferring any populated value.

    An empty incoming entry never displaces a populated held one. This is what
    keeps a partially failed run from emptying the file it is updating.
    """
    merged: RateMapping = {day: dict(rates) for day, rates in held.items()}
    for day, rates in fetched.items():
        if rates or not merged.get(day):
            merged[day] = dict(rates)
    return merged


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


def fetch_rate(
    session: requests.Session, day: date, app_id: str
) -> dict[str, float] | None:
    """Fetch rates for a single date, or None if the request failed.

    None and an empty dict are distinct: None means the request did not
    succeed and the date should be retried, while ``{}`` would mean the API
    answered with no rates.
    """
    url = f"{OPENEX_API_URL}{day}.json"
    try:
        response = session.get(url, params={"app_id": app_id}, timeout=OPENEX_TIMEOUT)
        response.raise_for_status()
        return response.json().get("rates", {})
    except requests.RequestException:
        logger.warning("Failed to fetch rates for %s", day, exc_info=True)
        return None


def fetch_rates(
    dates: Iterable[date],
    app_id: str,
    path: Path,
    *,
    refetch: bool = False,
    checkpoint_every: int = DEFAULT_CHECKPOINT_EVERY,
) -> RateMapping:
    """Bring the rate file at `path` up to date for `dates`.

    Only dates not already held are requested, unless `refetch` is set. The
    file is rewritten every `checkpoint_every` successful fetches and once more
    at the end, so an interrupted run keeps the requests it has already spent.

    Returns the full mapping as it now stands on disk.
    """
    held = load_rates(path)
    wanted = list(dates)
    pending = wanted if refetch else unfetched_dates(wanted, held)

    logger.info(
        "%d date(s) wanted, %d already held, %d to fetch",
        len(wanted),
        len(wanted) - len(pending),
        len(pending),
    )
    if not pending:
        return held

    session = _build_session()
    merged = dict(held)
    fetched: RateMapping = {}
    failures = 0

    for index, day in enumerate(tqdm(pending, desc="Fetching exchange rates"), start=1):
        rates = fetch_rate(session, day, app_id)
        if rates is None:
            failures += 1
            continue
        fetched[str(day)] = rates
        if index % checkpoint_every == 0:
            merged = merge_rates(merged, fetched)
            save_rates(merged, path)
            fetched = {}

    merged = merge_rates(merged, fetched)

    # The file being written is the only copy, so a result that holds fewer
    # populated dates than were loaded means something went wrong in this
    # function rather than upstream. Refuse rather than overwrite.
    if _populated(merged) < _populated(held):
        raise ValueError(
            f"Refusing to write {path}: the result holds "
            f"{_populated(merged)} populated date(s) against {_populated(held)} "
            "already on disk."
        )

    save_rates(merged, path)
    if failures:
        logger.warning(
            "%d of %d fetch(es) failed; those dates remain unheld and will be "
            "retried on the next run",
            failures,
            len(pending),
        )
    return merged


def _populated(rates: Mapping[str, Mapping]) -> int:
    """How many dates in a mapping carry actual rates."""
    return sum(1 for value in rates.values() if value)
