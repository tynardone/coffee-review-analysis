"""Where scraped reviews are read from and written to.

The corpus is a single ``reviews.csv`` with a ``reviews.json`` twin, updated in
place. Version history is left to git rather than encoded in filenames, so that
downstream code has one stable path to read.

The pipeline talks to a :class:`ReviewStore` rather than to files. Incremental
scraping needs to know what is already held and how fresh it is, and that
question is answered differently by a CSV than by a database.

The store owns the merge: :meth:`ReviewStore.upsert` receives only the records
that were fetched and combines them with what is already held. The pipeline
therefore never loads the whole corpus in order to write it back, and the
interface maps onto what a database does natively with
``INSERT ... ON CONFLICT DO UPDATE``.
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

        A URL mapped to ``None`` is held but of unknown freshness, and should
        be treated as stale. A URL absent from the mapping has never been
        scraped.
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
            # columns, which matters because the fields present vary with a
            # page's vintage: a 1997 review has no `bottom_line`, a 2026 one
            # has no bare `acidity`.
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
