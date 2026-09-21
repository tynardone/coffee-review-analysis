"""Tests for the review store.

The read side (`known_lastmods`) is new: nothing previously asked what was
already held. It is what incremental scraping decides against, so "held but
freshness unknown" has to be distinguishable from "not held at all" — both
would otherwise read as absent, one silently skipped and one silently
re-fetched.
"""

from datetime import date

import pandas as pd
import pytest

from coffee.storage import CsvReviewStore, ReviewStore


def seed(directory, rows):
    directory.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(directory / "reviews.csv", index=False)


def test_csv_store_satisfies_the_protocol(tmp_path):
    # runtime_checkable Protocols check method names only, but that is enough
    # to catch a rename that would break a second implementation.
    assert isinstance(CsvReviewStore(tmp_path), ReviewStore)


# --------------------------------------------------------------------------
# Reading what is already held
# --------------------------------------------------------------------------


def test_nothing_held_yet_is_empty_not_an_error(tmp_path):
    assert CsvReviewStore(tmp_path).known_lastmods() == {}


def test_reads_urls_and_lastmods(tmp_path):
    seed(
        tmp_path,
        [
            {"url": "u1", "sitemap_lastmod": "2026-09-01"},
            {"url": "u2", "sitemap_lastmod": "2014-10-02"},
        ],
    )
    assert CsvReviewStore(tmp_path).known_lastmods() == {
        "u1": date(2026, 9, 1),
        "u2": date(2014, 10, 2),
    }


def test_unparseable_lastmod_is_held_but_unknown(tmp_path):
    """None means 'held, freshness unproven' — the caller re-fetches it."""
    seed(
        tmp_path,
        [
            {"url": "u1", "sitemap_lastmod": "not a date"},
            {"url": "u2", "sitemap_lastmod": ""},
        ],
    )
    known = CsvReviewStore(tmp_path).known_lastmods()
    assert known == {"u1": None, "u2": None}
    assert set(known) == {"u1", "u2"}  # present, unlike a URL never seen


def test_data_predating_sitemap_discovery_reads_as_all_unknown(tmp_path):
    seed(tmp_path, [{"url": "u1", "rating": "93"}])
    assert CsvReviewStore(tmp_path).known_lastmods() == {"u1": None}


def test_data_without_urls_is_an_error(tmp_path):
    seed(tmp_path, [{"rating": "93"}])
    with pytest.raises(ValueError, match="no 'url' column"):
        CsvReviewStore(tmp_path).known_lastmods()


# --------------------------------------------------------------------------
# Writing: one file, updated in place
# --------------------------------------------------------------------------


def test_upsert_into_an_empty_store_writes_both_formats(tmp_path):
    store = CsvReviewStore(tmp_path)
    assert store.upsert([{"url": "u1", "rating": "93"}]) == 1
    assert store.csv_path.exists()


def test_upsert_replaces_by_url_and_keeps_the_rest(tmp_path):
    seed(tmp_path, [{"url": "u1", "rating": "90"}, {"url": "u2", "rating": "91"}])
    store = CsvReviewStore(tmp_path)
    assert store.upsert([{"url": "u1", "rating": "99"}]) == 2

    held = pd.read_csv(store.csv_path).set_index("url")["rating"].to_dict()
    assert held == {"u1": 99, "u2": 91}


def test_upsert_adds_new_urls(tmp_path):
    seed(tmp_path, [{"url": "u1", "rating": "90"}])
    store = CsvReviewStore(tmp_path)
    assert store.upsert([{"url": "u2", "rating": "91"}]) == 2


def test_upsert_unions_columns_across_page_vintages(tmp_path):
    """A 1997 review has no bottom_line; a 2026 one has no bare acidity.
    Carrying rows forward must not drop either side's fields."""
    seed(tmp_path, [{"url": "old", "acidity": "8"}])
    store = CsvReviewStore(tmp_path)
    store.upsert([{"url": "new", "acidity/structure": "9", "bottom_line": "nice"}])

    held = pd.read_csv(store.csv_path)
    assert {"acidity", "acidity/structure", "bottom_line"} <= set(held.columns)
    assert held.set_index("url").loc["old", "acidity"] == 8


def test_upsert_of_nothing_leaves_the_corpus_alone(tmp_path):
    seed(tmp_path, [{"url": "u1", "rating": "90"}])
    store = CsvReviewStore(tmp_path)
    assert store.upsert([]) == 1
    assert len(pd.read_csv(store.csv_path)) == 1


def test_replace_discards_what_was_held(tmp_path):
    seed(tmp_path, [{"url": "u1"}, {"url": "u2"}])
    store = CsvReviewStore(tmp_path)
    assert store.replace([{"url": "u3"}]) == 1
    assert pd.read_csv(store.csv_path)["url"].tolist() == ["u3"]


def test_replace_refuses_to_empty_the_corpus(tmp_path):
    """A full run that fetched nothing is a failure, not an instruction to
    delete everything."""
    seed(tmp_path, [{"url": "u1"}, {"url": "u2"}])
    store = CsvReviewStore(tmp_path)
    assert store.replace([]) == 2
    assert len(pd.read_csv(store.csv_path)) == 2


def test_round_trips_through_known_lastmods(tmp_path):
    store = CsvReviewStore(tmp_path)
    store.upsert([{"url": "u1", "sitemap_lastmod": date(2026, 9, 1)}])
    assert store.known_lastmods() == {"u1": date(2026, 9, 1)}


def test_rows_are_written_in_a_stable_order(tmp_path):
    """Sorted by URL so a re-run produces a minimal git diff rather than a
    reshuffled 10MB file."""
    store = CsvReviewStore(tmp_path)
    store.upsert([{"url": "b"}, {"url": "a"}, {"url": "c"}])
    assert pd.read_csv(store.csv_path)["url"].tolist() == ["a", "b", "c"]
