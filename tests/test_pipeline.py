"""Tests for the scrape: which pages to fetch, and what a bad one costs.

`plan_fetch` is the decision that turns a 29-minute run into a one-minute one,
so the cases that matter are the ones where it could wrongly SKIP a page — a
skipped page is a row that stays quietly wrong, while a wrongly re-fetched one
costs a single request.

`scrape_review` carries the failure contract. `scrape_all_reviews` awaits each
review in turn, so an exception escaping it abandons the remaining tasks and
writes nothing: one bad page out of ~9,000 would lose the run. Every failure
mode has to return None instead.
"""

import asyncio
from datetime import date

import pytest

from coffee import pipeline
from coffee.pipeline import plan_fetch

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
# One review: a bad page is a dropped row, never a dead run
# --------------------------------------------------------------------------


@pytest.fixture
def semaphore():
    return asyncio.Semaphore(2)


async def _ok_fetch(url, session, semaphore, retries=5):
    return "<html><h1 class='review-title'>A Coffee</h1></html>"


def test_returns_none_when_fetch_fails(monkeypatch, semaphore):
    async def failing_fetch(url, session, semaphore, retries=5):
        return None

    monkeypatch.setattr(pipeline, "fetch", failing_fetch)
    result = asyncio.run(pipeline.scrape_review("u", None, semaphore))
    assert result is None


def test_returns_none_when_parsing_raises(monkeypatch, semaphore):
    def exploding_parse(text):
        raise AttributeError("unexpected page shape")

    monkeypatch.setattr(pipeline, "fetch", _ok_fetch)
    monkeypatch.setattr(pipeline, "parse_html", exploding_parse)
    result = asyncio.run(pipeline.scrape_review("u", None, semaphore))
    assert result is None


def test_one_bad_page_does_not_abort_the_batch(monkeypatch, semaphore):
    """The regression this file exists for.

    Before the fix, the single failing page below aborted the gather and the
    caller collected zero results — not two.
    """

    def parse_one_bad(text):
        if "boom" in text:
            raise ValueError("malformed")
        return {"title": text}

    async def fetch_by_url(url, session, semaphore, retries=5):
        return url

    monkeypatch.setattr(pipeline, "fetch", fetch_by_url)
    monkeypatch.setattr(pipeline, "parse_html", parse_one_bad)

    async def run():
        urls = ["good-1", "boom", "good-2"]
        tasks = [pipeline.scrape_review(u, None, semaphore) for u in urls]
        return [r for r in await asyncio.gather(*tasks) if r is not None]

    results = asyncio.run(run())
    assert len(results) == 2
    assert {r["url"] for r in results} == {"good-1", "good-2"}


def test_successful_scrape_is_tagged_with_its_url(monkeypatch, semaphore):
    monkeypatch.setattr(pipeline, "fetch", _ok_fetch)
    result = asyncio.run(pipeline.scrape_review("http://x/r/1", None, semaphore))
    assert result is not None
    assert result["url"] == "http://x/r/1"
    assert result["title"] == "A Coffee"
