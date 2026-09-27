"""Tests for the review store.

`parsed_from` is what incremental parsing decides against: each row's
`scraped_at` names the saved page it came from, so a row whose page has since
been fetched again reads as out of date.
"""

import pandas as pd
import pytest

from coffee.storage import CsvReviewStore, ReviewStore


@pytest.fixture
def csv_path(tmp_path):
    return tmp_path / "reviews.csv"


def seed(csv_path, rows):
    pd.DataFrame(rows).to_csv(csv_path, index=False)


def test_csv_store_satisfies_the_protocol(csv_path):
    # runtime_checkable Protocols check method names only, but that is enough
    # to catch a rename that would break a second implementation.
    assert isinstance(CsvReviewStore(csv_path), ReviewStore)


# --------------------------------------------------------------------------
# Reading what is already held
# --------------------------------------------------------------------------


def test_nothing_held_yet_is_empty_not_an_error(csv_path):
    assert CsvReviewStore(csv_path).parsed_from() == {}


def test_reads_each_rows_scraped_at_as_written(csv_path):
    seed(
        csv_path,
        [
            {"url": "u1", "scraped_at": "2026-09-27T12:00:00+00:00"},
            {"url": "u2", "scraped_at": "2026-07-12T09:30:00"},
        ],
    )
    assert CsvReviewStore(csv_path).parsed_from() == {
        "u1": "2026-09-27T12:00:00+00:00",
        "u2": "2026-07-12T09:30:00",
    }


def test_a_missing_scraped_at_is_held_but_empty(csv_path):
    seed(csv_path, [{"url": "u1", "scraped_at": ""}, {"url": "u2", "scraped_at": "t"}])
    assert CsvReviewStore(csv_path).parsed_from() == {"u1": "", "u2": "t"}


def test_data_without_scraped_at_reads_as_all_empty(csv_path):
    seed(csv_path, [{"url": "u1", "rating": "93"}])
    assert CsvReviewStore(csv_path).parsed_from() == {"u1": ""}


def test_data_without_urls_is_an_error(csv_path):
    seed(csv_path, [{"rating": "93"}])
    with pytest.raises(ValueError, match="no 'url' column"):
        CsvReviewStore(csv_path).parsed_from()


# --------------------------------------------------------------------------
# Writing: one file, updated in place, never shrunk
# --------------------------------------------------------------------------


def test_upsert_into_an_empty_store_creates_the_file(tmp_path):
    store = CsvReviewStore(tmp_path / "raw" / "reviews.csv")
    assert store.upsert([{"url": "u1", "rating": "93"}]) == 1
    assert store.csv_path.exists()


def test_upsert_replaces_by_url_and_keeps_the_rest(csv_path):
    seed(csv_path, [{"url": "u1", "rating": "90"}, {"url": "u2", "rating": "91"}])
    store = CsvReviewStore(csv_path)
    assert store.upsert([{"url": "u1", "rating": "99"}]) == 2

    held = pd.read_csv(csv_path).set_index("url")["rating"].to_dict()
    assert held == {"u1": 99, "u2": 91}


def test_upsert_adds_new_urls(csv_path):
    seed(csv_path, [{"url": "u1", "rating": "90"}])
    assert CsvReviewStore(csv_path).upsert([{"url": "u2", "rating": "91"}]) == 2


def test_upsert_unions_columns_across_page_vintages(csv_path):
    """A 1997 review has no bottom_line; a 2026 one has no bare acidity.
    Carrying rows forward must not drop either side's fields."""
    seed(csv_path, [{"url": "old", "acidity": "8"}])
    store = CsvReviewStore(csv_path)
    store.upsert([{"url": "new", "acidity/structure": "9", "bottom_line": "nice"}])

    held = pd.read_csv(csv_path)
    assert {"acidity", "acidity/structure", "bottom_line"} <= set(held.columns)
    assert held.set_index("url").loc["old", "acidity"] == 8


def test_upsert_of_nothing_leaves_the_corpus_alone(csv_path):
    seed(csv_path, [{"url": "u1", "rating": "90"}])
    assert CsvReviewStore(csv_path).upsert([]) == 1
    assert len(pd.read_csv(csv_path)) == 1


def test_round_trips_through_parsed_from(csv_path):
    store = CsvReviewStore(csv_path)
    store.upsert([{"url": "u1", "scraped_at": "2026-09-27T12:00:00+00:00"}])
    assert store.parsed_from() == {"u1": "2026-09-27T12:00:00+00:00"}


def test_rows_are_written_in_a_stable_order(csv_path):
    """Sorted by URL so a re-run produces a minimal git diff rather than a
    reshuffled 10MB file."""
    store = CsvReviewStore(csv_path)
    store.upsert([{"url": "b"}, {"url": "a"}, {"url": "c"}])
    assert pd.read_csv(csv_path)["url"].tolist() == ["a", "b", "c"]
