"""The scraper's failure contract: a bad page is a dropped row, never a dead run.

`scrape_all_reviews` awaits each review in turn, so an exception escaping
`scrape_review` abandons the remaining tasks and writes nothing — one bad page
out of ~9,000 loses the run. These tests pin the contract: every failure mode
returns None.
"""

import asyncio

import pytest

from coffee import review


@pytest.fixture
def semaphore():
    return asyncio.Semaphore(2)


async def _ok_fetch(url, session, semaphore, retries=5):
    return "<html><h1 class='review-title'>A Coffee</h1></html>"


def test_returns_none_when_fetch_fails(monkeypatch, semaphore):
    async def failing_fetch(url, session, semaphore, retries=5):
        return None

    monkeypatch.setattr(review, "fetch", failing_fetch)
    result = asyncio.run(review.scrape_review("u", None, semaphore))
    assert result is None


def test_returns_none_when_parsing_raises(monkeypatch, semaphore):
    def exploding_parse(text):
        raise AttributeError("unexpected page shape")

    monkeypatch.setattr(review, "fetch", _ok_fetch)
    monkeypatch.setattr(review, "parse_html", exploding_parse)
    result = asyncio.run(review.scrape_review("u", None, semaphore))
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

    monkeypatch.setattr(review, "fetch", fetch_by_url)
    monkeypatch.setattr(review, "parse_html", parse_one_bad)

    async def run():
        urls = ["good-1", "boom", "good-2"]
        tasks = [review.scrape_review(u, None, semaphore) for u in urls]
        return [r for r in await asyncio.gather(*tasks) if r is not None]

    results = asyncio.run(run())
    assert len(results) == 2
    assert {r["url"] for r in results} == {"good-1", "good-2"}


def test_successful_scrape_is_tagged_with_its_url(monkeypatch, semaphore):
    monkeypatch.setattr(review, "fetch", _ok_fetch)
    result = asyncio.run(review.scrape_review("http://x/r/1", None, semaphore))
    assert result is not None
    assert result["url"] == "http://x/r/1"
    assert result["title"] == "A Coffee"
