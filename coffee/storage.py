"""Where scraped reviews are read from and written to.

The pipeline talks to a :class:`ReviewStore` rather than to files, for two
reasons. Incremental scraping needs to ask what is already held and how fresh
it is — a read the pipeline has never had — and that question has a different
answer for a directory of CSVs than for a database. Naming the seam now means
the Postgres implementation arrives without touching the pipeline.

The protocol is two methods because that is all incremental scraping needs.
Guessing at a wider interface before a second implementation exists is how
abstractions end up shaped like their first and only caller.
"""

import logging
import re
from collections.abc import Iterable, Mapping
from datetime import date, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import pandas as pd

__all__ = ["CsvReviewStore", "ReviewStore", "dated_filename"]

logger = logging.getLogger(__name__)

# Only ISO-dated snapshots are candidates for "the most recent run". The legacy
# 25072024_reviews.csv is DDMMYYYY, and sorts AFTER every ISO name lexically —
# a plain max() over the directory would silently pick a 2024 file as newest.
SNAPSHOT_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})_reviews\.csv$")


def dated_filename(stem: str, suffix: str) -> str:
    """``reviews``, ``csv`` -> ``2026-09-20_reviews.csv``."""
    return f"{datetime.now().strftime('%Y-%m-%d')}_{stem}.{suffix}"


@runtime_checkable
class ReviewStore(Protocol):
    """What the pipeline needs of a place to keep reviews."""

    def known_lastmods(self) -> dict[str, date | None]:
        """Every review already held, mapped to its recorded ``sitemap_lastmod``.

        A URL present with ``None`` means "held, but freshness unknown" — the
        caller should treat it as stale, since nothing proves it is current.
        """
        ...

    def save(self, records: Iterable[Mapping[str, Any]]) -> int:
        """Persist a COMPLETE set of reviews and return how many were written.

        Callers pass everything, not just what changed: each run leaves behind
        a self-contained dataset rather than a delta that only makes sense
        alongside its predecessors.
        """
        ...


class CsvReviewStore:
    """Dated CSV + JSON snapshots in a directory, one pair per run."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def latest_snapshot(self) -> Path | None:
        """The most recent ISO-dated snapshot, or None if there is none yet."""
        dated = [
            (match.group(1), path)
            for path in self.directory.glob("*_reviews.csv")
            if (match := SNAPSHOT_PATTERN.match(path.name))
        ]
        return max(dated)[1] if dated else None

    def known_lastmods(self) -> dict[str, date | None]:
        snapshot = self.latest_snapshot()
        if snapshot is None:
            return {}

        frame = pd.read_csv(snapshot, usecols=lambda c: c in {"url", "sitemap_lastmod"})
        if "url" not in frame.columns:
            raise ValueError(f"{snapshot} has no 'url' column.")
        if "sitemap_lastmod" not in frame.columns:
            # Snapshots predating sitemap discovery have no freshness to report.
            logger.info("%s predates sitemap_lastmod; treating all as stale", snapshot)
            return dict.fromkeys(frame["url"].astype(str), None)

        parsed = pd.to_datetime(frame["sitemap_lastmod"], errors="coerce")
        return {
            str(url): (None if pd.isna(stamp) else stamp.date())
            for url, stamp in zip(frame["url"], parsed, strict=True)
        }

    def save(self, records: Iterable[Mapping[str, Any]]) -> int:
        rows = list(records)
        if not rows:
            logger.warning("No reviews to write; nothing saved.")
            return 0

        self.directory.mkdir(parents=True, exist_ok=True)
        frame = pd.DataFrame(rows)
        csv_path = self.directory / dated_filename("reviews", "csv")
        json_path = self.directory / dated_filename("reviews", "json")
        frame.to_csv(csv_path, index=False)
        frame.to_json(json_path, orient="records")
        logger.info("Wrote %d reviews to %s and %s", len(frame), csv_path, json_path)
        return len(frame)
