"""Tests for the review store.

The read side (`known_lastmods`) is new: nothing in the pipeline previously
asked what was already held. It is what incremental scraping will decide
against, so "held but freshness unknown" has to be distinguishable from "not
held at all" — both would otherwise read as absent and be silently re-fetched
or silently skipped.
"""

from datetime import date

import pandas as pd
import pytest

from coffee.storage import CsvReviewStore, ReviewStore, dated_filename


def write_snapshot(directory, name, rows):
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(directory / name, index=False)


def test_csv_store_satisfies_the_protocol():
    assert isinstance(
        CsvReviewStore(  # runtime_checkable: method names only
            __import__("pathlib").Path(".")
        ),
        ReviewStore,
    )


def test_dated_filename_is_iso():
    name = dated_filename("reviews", "csv")
    assert name.endswith("_reviews.csv")
    date.fromisoformat(name.split("_")[0])  # raises if not ISO


# --------------------------------------------------------------------------
# Reading what is already held
# --------------------------------------------------------------------------


def test_no_snapshot_yet_is_empty_not_an_error(tmp_path):
    assert CsvReviewStore(tmp_path).known_lastmods() == {}


def test_reads_urls_and_lastmods(tmp_path):
    write_snapshot(
        tmp_path,
        "2026-09-19_reviews.csv",
        [
            {"url": "u1", "sitemap_lastmod": "2026-09-01"},
            {"url": "u2", "sitemap_lastmod": "2014-10-02"},
        ],
    )
    assert CsvReviewStore(tmp_path).known_lastmods() == {
        "u1": date(2026, 9, 1),
        "u2": date(2014, 10, 2),
    }


def test_the_most_recent_snapshot_wins(tmp_path):
    write_snapshot(
        tmp_path,
        "2026-07-12_reviews.csv",
        [{"url": "u1", "sitemap_lastmod": "2026-01-01"}],
    )
    write_snapshot(
        tmp_path,
        "2026-09-19_reviews.csv",
        [{"url": "u1", "sitemap_lastmod": "2026-09-01"}],
    )
    assert CsvReviewStore(tmp_path).known_lastmods()["u1"] == date(2026, 9, 1)


def test_a_legacy_ddmmyyyy_filename_cannot_win(tmp_path):
    """THE TRAP. '25072024_reviews.csv' sorts AFTER every ISO name lexically,
    so a plain max() over the directory picks a 2024 file as the newest."""
    write_snapshot(
        tmp_path,
        "2026-09-19_reviews.csv",
        [{"url": "current", "sitemap_lastmod": "2026-09-01"}],
    )
    write_snapshot(
        tmp_path,
        "25072024_reviews.csv",
        [{"url": "legacy", "sitemap_lastmod": "2024-07-25"}],
    )
    assert set(CsvReviewStore(tmp_path).known_lastmods()) == {"current"}


def test_unparseable_lastmod_is_held_but_unknown(tmp_path):
    """None means 'held, freshness unproven' — the caller re-fetches it."""
    write_snapshot(
        tmp_path,
        "2026-09-19_reviews.csv",
        [
            {"url": "u1", "sitemap_lastmod": "not a date"},
            {"url": "u2", "sitemap_lastmod": ""},
        ],
    )
    known = CsvReviewStore(tmp_path).known_lastmods()
    assert known == {"u1": None, "u2": None}
    assert set(known) == {"u1", "u2"}  # present, unlike a URL never seen


def test_a_snapshot_predating_sitemap_discovery_reads_as_all_unknown(tmp_path):
    write_snapshot(tmp_path, "2026-09-19_reviews.csv", [{"url": "u1", "rating": "93"}])
    assert CsvReviewStore(tmp_path).known_lastmods() == {"u1": None}


def test_a_snapshot_without_urls_is_an_error(tmp_path):
    write_snapshot(tmp_path, "2026-09-19_reviews.csv", [{"rating": "93"}])
    with pytest.raises(ValueError, match="no 'url' column"):
        CsvReviewStore(tmp_path).known_lastmods()


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


def test_save_writes_a_complete_dated_pair(tmp_path):
    store = CsvReviewStore(tmp_path)
    assert store.save([{"url": "u1", "rating": "93"}]) == 1
    written = sorted(p.name for p in tmp_path.iterdir())
    assert len(written) == 2
    assert written[0].endswith("_reviews.csv") and written[1].endswith("_reviews.json")


def test_save_round_trips_through_known_lastmods(tmp_path):
    store = CsvReviewStore(tmp_path)
    store.save([{"url": "u1", "sitemap_lastmod": date(2026, 9, 1)}])
    assert store.known_lastmods() == {"u1": date(2026, 9, 1)}


def test_saving_nothing_writes_nothing(tmp_path):
    assert CsvReviewStore(tmp_path).save([]) == 0
    assert not list(tmp_path.glob("*"))
