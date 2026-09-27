"""Tests for the parse: saved pages to rows, with no network.

The cases that matter: only pages not yet parsed are read by default, a page
fetched again is picked up, one bad page never costs the rest, and no row is
ever removed.
"""

from datetime import date

import pandas as pd
import pytest

from coffee import parse as parse_module
from coffee.page_store import PageStore
from coffee.parse import parse_saved_reviews
from coffee.review_store import CsvReviewStore

JAN = date(2026, 1, 1)
JUN = date(2026, 6, 1)

A = "https://www.coffeereview.com/review/a/"
B = "https://www.coffeereview.com/review/b/"
C = "https://www.coffeereview.com/review/c/"


@pytest.fixture
def pages(tmp_path):
    return PageStore(tmp_path / "bronze")


PAGE = "<html><h1 class='review-title'>{}</h1></html>"


@pytest.fixture
def store(tmp_path):
    return CsvReviewStore(tmp_path / "reviews.csv")


def rows(store):
    return pd.read_csv(store.csv_path).set_index("url")


def test_parse_writes_a_row_per_page_with_its_provenance(pages, store):
    pages.save(A, PAGE.format("Coffee A"), JAN, fetched_at="t1")

    result = parse_saved_reviews(pages, store)

    assert (result.parsed, result.total) == (1, 1)
    row = rows(store).loc[A]
    assert row["title"] == "Coffee A"
    assert row["sitemap_lastmod"] == "2026-01-01"
    assert row["scraped_at"] == "t1"


def test_parse_skips_pages_already_parsed_from_the_same_fetch(pages, store):
    pages.save(A, PAGE.format("A"), JAN, fetched_at="t1")
    parse_saved_reviews(pages, store)
    pages.save(B, PAGE.format("B"), JAN, fetched_at="t1")

    assert parse_saved_reviews(pages, store).parsed == 1  # only B


def test_parse_picks_up_a_page_fetched_again(pages, store):
    pages.save(A, PAGE.format("old title"), JAN, fetched_at="t1")
    parse_saved_reviews(pages, store)
    pages.save(A, PAGE.format("new title"), JUN, fetched_at="t2")

    assert parse_saved_reviews(pages, store).parsed == 1
    assert rows(store).loc[A, "title"] == "new title"


def test_full_reparses_every_saved_page(pages, store):
    pages.save(A, PAGE.format("A"), JAN, fetched_at="t1")
    pages.save(B, PAGE.format("B"), JAN, fetched_at="t1")
    parse_saved_reviews(pages, store)

    assert parse_saved_reviews(pages, store).parsed == 0
    assert parse_saved_reviews(pages, store, full=True).parsed == 2


def test_limit_caps_the_pages_parsed(pages, store):
    for url in (A, B, C):
        pages.save(url, PAGE.format(url), JAN, fetched_at="t1")
    assert parse_saved_reviews(pages, store, limit=2).parsed == 2
    assert parse_saved_reviews(pages, store).parsed == 1  # the rest, next time


def test_a_page_that_fails_to_parse_keeps_its_old_row(pages, store, monkeypatch):
    pages.save(A, PAGE.format("good A"), JAN, fetched_at="t1")
    pages.save(B, PAGE.format("B"), JAN, fetched_at="t1")
    parse_saved_reviews(pages, store)

    def parse_that_fails_on_a(text):
        if "A" in text:
            raise ValueError("malformed")
        return {"title": "B again"}

    monkeypatch.setattr(parse_module, "parse_html", parse_that_fails_on_a)
    result = parse_saved_reviews(pages, store, full=True)

    assert result.failed == [A]
    assert rows(store).loc[A, "title"] == "good A"
    assert rows(store).loc[B, "title"] == "B again"


def test_rows_without_a_saved_page_are_never_removed(pages, store):
    """A review gone from the site before bronze existed survives a re-parse."""
    store.upsert([{"url": C, "title": "only copy"}])
    pages.save(A, PAGE.format("A"), JAN, fetched_at="t1")

    parse_saved_reviews(pages, store, full=True)

    assert rows(store).loc[C, "title"] == "only copy"
    assert len(rows(store)) == 2
