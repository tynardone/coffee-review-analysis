"""Where parsed reviews are read from and written to.

The corpus is a single ``reviews.csv``, updated in place. The parse step talks
to a :class:`ReviewStore` rather than to the file, so a database could replace
the CSV without changing the parse.

Rows are only ever added or updated, never removed. A review that disappears
from the site has no saved page to re-parse, so its row is the only copy left.
"""

import logging
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any, Final, Protocol, runtime_checkable

import pandas as pd

__all__ = [
    "SCRAPED_AT_COLUMN",
    "SITEMAP_LASTMOD_COLUMN",
    "URL_COLUMN",
    "CsvReviewStore",
    "ReviewStore",
]

logger = logging.getLogger(__name__)

# Columns every row carries, whatever the page it came from. The parse step
# writes them; this store reads them back.
URL_COLUMN: Final = "url"
SITEMAP_LASTMOD_COLUMN: Final = "sitemap_lastmod"
SCRAPED_AT_COLUMN: Final = "scraped_at"


@runtime_checkable
class ReviewStore(Protocol):
    """What the parse step needs of a place to keep reviews."""

    def parsed_from(self) -> dict[str, str]:
        """Every review held, mapped to the ``scraped_at`` it was parsed from.

        ``scraped_at`` is the fetch time of the saved page a row came from, so
        comparing it with the bronze manifest shows which rows are out of date.
        A row with no ``scraped_at`` maps to an empty string.
        """
        ...

    def upsert(self, records: Iterable[Mapping[str, Any]]) -> int:
        """Add or replace records by URL, leaving everything else untouched.

        Returns the total number of reviews held afterwards, not the number
        written, so callers can report the size of the corpus.
        """
        ...


class CsvReviewStore:
    """A single ``reviews.csv``, updated in place."""

    def __init__(self, csv_path: Path) -> None:
        self.csv_path = csv_path

    # -- reading -----------------------------------------------------------

    def parsed_from(self) -> dict[str, str]:
        if not self.csv_path.exists():
            return {}

        frame = pd.read_csv(
            self.csv_path,
            usecols=lambda column: column in {URL_COLUMN, SCRAPED_AT_COLUMN},
            dtype=str,
            keep_default_na=False,
        )
        if URL_COLUMN not in frame.columns:
            raise ValueError(f"{self.csv_path} has no {URL_COLUMN!r} column.")
        if SCRAPED_AT_COLUMN not in frame.columns:
            return dict.fromkeys(frame[URL_COLUMN], "")
        return dict(zip(frame[URL_COLUMN], frame[SCRAPED_AT_COLUMN], strict=True))

    def _load(self) -> pd.DataFrame:
        return pd.read_csv(self.csv_path) if self.csv_path.exists() else pd.DataFrame()

    # -- writing -----------------------------------------------------------

    def upsert(self, records: Iterable[Mapping[str, Any]]) -> int:
        incoming = pd.DataFrame(list(records))
        if incoming.empty:
            held = self._load()
            logger.info("Nothing to write; %d reviews unchanged", len(held))
            return len(held)

        existing = self._load()
        if not existing.empty:
            # Drop the rows being replaced, then append. concat unions the
            # columns, which matters because the fields present vary with a
            # page's vintage: a 1997 review has no `bottom_line`, a 2026 one
            # has no bare `acidity`.
            kept = existing[~existing[URL_COLUMN].isin(set(incoming[URL_COLUMN]))]
            merged = pd.concat([kept, incoming], ignore_index=True)
        else:
            merged = incoming
        return self._write(merged)

    def _write(self, frame: pd.DataFrame) -> int:
        self.csv_path.parent.mkdir(parents=True, exist_ok=True)
        frame = frame.sort_values(URL_COLUMN).reset_index(drop=True)
        frame.to_csv(self.csv_path, index=False)
        logger.info("Wrote %d reviews to %s", len(frame), self.csv_path)
        return len(frame)
