"""Where scraped reviews are read from and written to.

ONE SOURCE OF TRUTH
    A single ``reviews.csv`` (plus a ``reviews.json`` twin), updated in place.
    History is git's job, not the filename's: dated snapshots were doing both
    jobs at once, which meant three full copies of the corpus in the working
    tree and no single file you could point downstream code at.

THE SEAM
    The pipeline talks to a :class:`ReviewStore`, not to files. Incremental
    scraping has to ask what is already held and how fresh it is, and that
    question has a different answer for a CSV than for a database.

    The store OWNS THE MERGE. :meth:`ReviewStore.upsert` takes only the records
    that were fetched and is responsible for combining them with what is
    already there. That keeps the pipeline from having to load the whole corpus
    into memory just to write it back out, and it maps onto exactly what a
    database does natively (``INSERT ... ON CONFLICT DO UPDATE``) instead of
    forcing a read-modify-write through the caller.
"""

import logging
from collections.abc import Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pandas as pd

__all__ = ["CsvReviewStore", "ReviewStore"]

logger = logging.getLogger(__name__)

URL_COLUMN = "url"
LASTMOD_COLUMN = "sitemap_lastmod"


@runtime_checkable
class ReviewStore(Protocol):
    """What the pipeline needs of a place to keep reviews."""

    def known_lastmods(self) -> dict[str, date | None]:
        """Every review already held, mapped to its recorded ``sitemap_lastmod``.

        A URL present with ``None`` means "held, but freshness unknown" — the
        caller should treat it as stale, since nothing proves it is current.
        Absent from the mapping means never scraped.
        """
        ...

    def upsert(self, records: Iterable[Mapping[str, Any]]) -> int:
        """Add or replace records by URL, leaving everything else untouched.

        Returns the total number of reviews held afterwards, not the number
        written, so callers can report the size of the corpus.
        """
        ...

    def replace(self, records: Iterable[Mapping[str, Any]]) -> int:
        """Discard what is held and keep exactly `records`. Used by ``--full``."""
        ...


class CsvReviewStore:
    """A single ``reviews.csv`` + ``reviews.json`` pair, updated in place."""

    def __init__(self, directory: Path, stem: str = "reviews") -> None:
        self.directory = directory
        self.csv_path = directory / f"{stem}.csv"
        self.json_path = directory / f"{stem}.json"

    # -- reading -----------------------------------------------------------

    def known_lastmods(self) -> dict[str, date | None]:
        if not self.csv_path.exists():
            return {}

        frame = pd.read_csv(
            self.csv_path,
            usecols=lambda column: column in {URL_COLUMN, LASTMOD_COLUMN},
        )
        if URL_COLUMN not in frame.columns:
            raise ValueError(f"{self.csv_path} has no {URL_COLUMN!r} column.")
        if LASTMOD_COLUMN not in frame.columns:
            # Data predating sitemap discovery has no freshness to report.
            logger.info(
                "%s predates %s; treating all as stale", self.csv_path, LASTMOD_COLUMN
            )
            return dict.fromkeys(frame[URL_COLUMN].astype(str), None)

        stamps = pd.to_datetime(frame[LASTMOD_COLUMN], errors="coerce")
        return {
            str(url): (None if pd.isna(stamp) else stamp.date())
            for url, stamp in zip(frame[URL_COLUMN], stamps, strict=True)
        }

    def _load(self) -> pd.DataFrame:
        return pd.read_csv(self.csv_path) if self.csv_path.exists() else pd.DataFrame()

    # -- writing -----------------------------------------------------------

    def upsert(self, records: Iterable[Mapping[str, Any]]) -> int:
        incoming = pd.DataFrame(list(records))
        if incoming.empty:
            held = self._load()
            logger.info("Nothing fetched; %d reviews unchanged", len(held))
            return len(held)

        existing = self._load()
        if not existing.empty:
            # Drop the rows being replaced, then append. concat unions the
            # columns, which matters because fields come and go with a page's
            # vintage -- a 1997 review has no `bottom_line`, a 2026 one has no
            # bare `acidity`.
            kept = existing[~existing[URL_COLUMN].isin(set(incoming[URL_COLUMN]))]
            merged = pd.concat([kept, incoming], ignore_index=True)
        else:
            merged = incoming
        return self._write(merged)

    def replace(self, records: Iterable[Mapping[str, Any]]) -> int:
        frame = pd.DataFrame(list(records))
        if frame.empty:
            logger.warning("Refusing to replace the corpus with nothing.")
            return len(self._load())
        return self._write(frame)

    def _write(self, frame: pd.DataFrame) -> int:
        self.directory.mkdir(parents=True, exist_ok=True)
        frame = frame.sort_values(URL_COLUMN).reset_index(drop=True)
        frame.to_csv(self.csv_path, index=False)
        frame.to_json(self.json_path, orient="records", indent=2)
        logger.info("Wrote %d reviews to %s", len(frame), self.csv_path)
        return len(frame)
