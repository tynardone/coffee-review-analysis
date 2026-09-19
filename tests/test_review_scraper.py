"""The scraper's failure contract: a bad page is a dropped row, never a dead run.

`scrape_all_reviews` awaits each review one at a time, so any exception that
escapes `scrape_review` propagates out of the loop, abandons the remaining
tasks, and returns before anything is written. On a ~9,000-URL scrape that turns
one malformed page into a total loss. These tests pin the contract that prevents
it: every failure mode returns None.
"""

import asyncio

import pytest

from coffee import review_scraper


@pytest.fixture
def semaphore():
    return asyncio.Semaphore(2)


async def _ok_fetch(url, session, semaphore, retries=5):
    return "<html><h1 class='review-title'>A Coffee</h1></html>"


def test_returns_none_when_fetch_fails(monkeypatch, semaphore):
    async def failing_fetch(url, session, semaphore, retries=5):
        return None

    monkeypatch.setattr(review_scraper, "fetch", failing_fetch)
    result = asyncio.run(review_scraper.scrape_review("u", None, semaphore))
    assert result is None


def test_returns_none_when_parsing_raises(monkeypatch, semaphore):
    def exploding_parse(text):
        raise AttributeError("unexpected page shape")

    monkeypatch.setattr(review_scraper, "fetch", _ok_fetch)
    monkeypatch.setattr(review_scraper, "parse_html", exploding_parse)
    result = asyncio.run(review_scraper.scrape_review("u", None, semaphore))
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

    monkeypatch.setattr(review_scraper, "fetch", fetch_by_url)
    monkeypatch.setattr(review_scraper, "parse_html", parse_one_bad)

    async def run():
        urls = ["good-1", "boom", "good-2"]
        tasks = [review_scraper.scrape_review(u, None, semaphore) for u in urls]
        return [r for r in await asyncio.gather(*tasks) if r is not None]

    results = asyncio.run(run())
    assert len(results) == 2
    assert {r["url"] for r in results} == {"good-1", "good-2"}


def test_successful_scrape_is_tagged_with_its_url(monkeypatch, semaphore):
    monkeypatch.setattr(review_scraper, "fetch", _ok_fetch)
    result = asyncio.run(review_scraper.scrape_review("http://x/r/1", None, semaphore))
    assert result is not None
    assert result["url"] == "http://x/r/1"
    assert result["title"] == "A Coffee"
