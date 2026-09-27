"""Tests for the scrape: which pages to fetch, and what a bad one costs.

`plan_fetch` is the decision that turns a 29-minute run into a one-minute one,
so the cases that matter are the ones where it could wrongly SKIP a page — a
skipped page stays quietly stale, while a wrongly re-fetched one costs a single
request.

The rest pin the failure contract: one bad page is reported and skipped, never
the end of the run.
"""

import asyncio
from datetime import date

import pytest

from coffee import scrape as scrape_module
from coffee.page_store import PageStore
from coffee.scrape import plan_fetch, scrape_all_reviews

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

    monkeypatch.setattr(scrape_module, "get_review_urls", fake_discovery)
    monkeypatch.setattr(scrape_module, "fetch", fake_fetch)
    Site.fetched = []
    return Site


def scrape(pages, **kwargs):
    return asyncio.run(scrape_all_reviews(pages, concurrency=2, **kwargs))


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

    monkeypatch.setattr(scrape_module, "fetch", exploding_fetch)
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
