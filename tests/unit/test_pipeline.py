"""Tests for the scrape and the parse.

`plan_fetch` is the decision that turns a 29-minute run into a one-minute one,
so the cases that matter are the ones where it could wrongly SKIP a page — a
skipped page stays quietly stale, while a wrongly re-fetched one costs a single
request.

The scrape and parse tests pin the failure contract: one bad page is reported
and skipped, never the end of the run.
"""

import asyncio
from datetime import date

import pandas as pd
import pytest

from coffee import pipeline
from coffee.bronze import PageStore
from coffee.pipeline import parse_saved_reviews, plan_fetch, scrape_all_reviews
from coffee.storage import CsvReviewStore

JAN = date(2026, 1, 1)
JUN = date(2026, 6, 1)


def test_a_url_never_seen_is_fetched():
    to_fetch, _ = plan_fetch({"new": JAN}, {})
    assert to_fetch == {"new"}


def test_an_unchanged_url_is_skipped():
    to_fetch, _ = plan_fetch({"u": JAN}, {"u": JAN})
    assert to_fetch == set()


def test_a_newer_lastmod_is_fetched():
    to_fetch, _ = plan_fetch({"u": JUN}, {"u": JAN})
    assert to_fetch == {"u"}


def test_an_older_lastmod_upstream_is_not_fetched():
    """The site going backwards is odd, but it is not evidence of change."""
    to_fetch, _ = plan_fetch({"u": JAN}, {"u": JUN})
    assert to_fetch == set()


def test_unknown_upstream_date_is_fetched():
    """Cannot prove it is unchanged, so assume it changed."""
    to_fetch, _ = plan_fetch({"u": None}, {"u": JAN})
    assert to_fetch == {"u"}


def test_unknown_held_date_is_fetched():
    """Rows predating sitemap discovery have no recorded freshness."""
    to_fetch, _ = plan_fetch({"u": JAN}, {"u": None})
    assert to_fetch == {"u"}


def test_urls_no_longer_listed_are_reported_not_fetched():
    to_fetch, retired = plan_fetch({"live": JAN}, {"live": JAN, "gone": JAN})
    assert to_fetch == set()
    assert retired == {"gone"}


def test_retired_urls_are_never_silently_dropped():
    """A review that disappears upstream cannot be re-fetched, so the held copy
    is the only one. plan_fetch reports it; nothing deletes it."""
    _, retired = plan_fetch({}, {"gone": JAN})
    assert retired == {"gone"}


def test_a_realistic_mixture():
    discovered = {"unchanged": JAN, "changed": JUN, "new": JUN, "undated": None}
    known = {"unchanged": JAN, "changed": JAN, "gone": JAN, "undated": JAN}
    to_fetch, retired = plan_fetch(discovered, known)
    assert to_fetch == {"changed", "new", "undated"}
    assert retired == {"gone"}


# --------------------------------------------------------------------------
# Scrape: download to bronze, never parse
# --------------------------------------------------------------------------

A = "https://www.coffeereview.com/review/a/"
B = "https://www.coffeereview.com/review/b/"
C = "https://www.coffeereview.com/review/c/"


@pytest.fixture
def pages(tmp_path):
    return PageStore(tmp_path / "bronze")


@pytest.fixture
def site(monkeypatch):
    """A fake site: discovery lists `listed`, and fetch serves `served`.

    A URL missing from `served` fails to fetch. Every fetch is recorded.
    """

    class Site:
        listed: dict = {}
        served: dict = {}
        fetched: list = []

    async def fake_discovery(session, semaphore):
        return dict(Site.listed)

    async def fake_fetch(url, session, semaphore, retries=5):
        Site.fetched.append(url)
        return Site.served.get(url)

    monkeypatch.setattr(pipeline, "get_review_urls", fake_discovery)
    monkeypatch.setattr(pipeline, "fetch", fake_fetch)
    Site.fetched = []
    return Site


def scrape(pages, **kwargs):
    return asyncio.run(scrape_all_reviews(pages, **kwargs))


def test_scrape_saves_each_page_with_its_lastmod(pages, site):
    site.listed = {A: JAN, B: JUN}
    site.served = {A: "<html>a</html>", B: "<html>b</html>"}

    result = scrape(pages)

    assert result.saved == 2
    assert pages.known_lastmods() == {A: JAN, B: JUN}
    assert pages.read(pages.manifest()[A]) == "<html>a</html>"


def test_a_failed_fetch_is_not_saved_and_the_rest_are(pages, site):
    site.listed = {A: JAN, B: JAN, C: JAN}
    site.served = {A: "a", C: "c"}  # B fails

    result = scrape(pages)

    assert result.failed == [B]
    assert set(pages.manifest()) == {A, C}


def test_an_exception_while_fetching_does_not_abort_the_run(pages, site, monkeypatch):
    site.listed = {A: JAN, B: JAN}

    async def exploding_fetch(url, session, semaphore, retries=5):
        if url == A:
            raise RuntimeError("unexpected")
        return "b"

    monkeypatch.setattr(pipeline, "fetch", exploding_fetch)
    result = scrape(pages)
    assert result.failed == [A]
    assert set(pages.manifest()) == {B}


def test_a_second_run_fetches_only_what_changed(pages, site):
    site.listed = {A: JAN, B: JAN}
    site.served = {A: "a", B: "b"}
    scrape(pages)

    site.fetched = []
    site.listed = {A: JAN, B: JUN}  # B changed upstream
    scrape(pages)

    assert site.fetched == [B]


def test_full_refetches_everything(pages, site):
    site.listed = {A: JAN, B: JAN}
    site.served = {A: "a", B: "b"}
    scrape(pages)

    site.fetched = []
    scrape(pages, full=True)
    assert sorted(site.fetched) == [A, B]


def test_limit_caps_the_pages_fetched(pages, site):
    site.listed = {A: JAN, B: JAN, C: JAN}
    site.served = {A: "a", B: "b", C: "c"}
    assert scrape(pages, limit=2).saved == 2


def test_a_page_gone_from_the_sitemap_is_reported_and_kept(pages, site):
    site.listed = {A: JAN, B: JAN}
    site.served = {A: "a", B: "b"}
    scrape(pages)

    site.listed = {A: JAN}
    result = scrape(pages)
    assert result.retired == 1
    assert set(pages.manifest()) == {A, B}


# --------------------------------------------------------------------------
# Parse: saved pages to rows, no network
# --------------------------------------------------------------------------

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

    monkeypatch.setattr(pipeline, "parse_html", parse_that_fails_on_a)
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
