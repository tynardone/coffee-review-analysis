"""Tests for the scrape: which pages to fetch, and what a bad one costs.

`plan_fetch` is the decision that turns a 29-minute run into a one-minute one,
so the cases that matter are the ones where it could wrongly SKIP a page — a
skipped page stays quietly stale, while a wrongly re-fetched one costs a single
request. It must also never re-fetch the same page run after run.

The rest pin the failure contract: one bad page is reported and skipped, never
the end of the run.
"""

import asyncio
from datetime import UTC, date, datetime, timedelta

import pytest

from coffee import scrape as scrape_module
from coffee.page_store import PageStore
from coffee.scrape import plan_fetch, scrape_all_reviews

DAY = timedelta(days=1)
LOOKBACK = timedelta(hours=24)

# When we downloaded a page, and times relative to it.
FETCHED = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
LONG_BEFORE = FETCHED - 30 * DAY
AFTER = FETCHED + timedelta(hours=3)


def plan(discovered, fetched):
    return plan_fetch(discovered, fetched, LOOKBACK)


def test_a_url_never_seen_is_fetched():
    to_fetch, _ = plan({"new": LONG_BEFORE}, {})
    assert to_fetch == {"new"}


def test_a_page_unchanged_since_its_download_is_skipped():
    to_fetch, _ = plan({"u": LONG_BEFORE}, {"u": FETCHED})
    assert to_fetch == set()


def test_an_edit_after_the_download_is_fetched():
    to_fetch, _ = plan({"u": FETCHED + 10 * DAY}, {"u": FETCHED})
    assert to_fetch == {"u"}


def test_an_edit_later_on_the_same_day_as_the_download_is_fetched():
    """The bug this rule fixes: comparing dates alone saw no change here."""
    to_fetch, _ = plan({"u": AFTER}, {"u": FETCHED})
    assert AFTER.date() == FETCHED.date()
    assert to_fetch == {"u"}


def test_an_edit_just_before_the_download_is_fetched_once_more():
    """The site's cache may have served the pre-edit page; check again."""
    to_fetch, _ = plan({"u": FETCHED - timedelta(minutes=5)}, {"u": FETCHED})
    assert to_fetch == {"u"}


def test_an_edit_outside_the_lookback_is_trusted():
    to_fetch, _ = plan({"u": FETCHED - LOOKBACK - timedelta(minutes=1)}, {"u": FETCHED})
    assert to_fetch == set()


def test_the_re_fetch_does_not_repeat():
    """Once re-fetched, the download is well past the edit."""
    edited = FETCHED - timedelta(minutes=5)
    refetched = FETCHED + 2 * DAY
    to_fetch, _ = plan({"u": edited}, {"u": refetched})
    assert to_fetch == set()


def test_no_lookback_means_exactly_after_the_download():
    to_fetch, _ = plan_fetch(
        {"before": FETCHED - timedelta(seconds=1), "after": AFTER},
        {"before": FETCHED, "after": FETCHED},
        timedelta(0),
    )
    assert to_fetch == {"after"}


def test_an_unknown_lastmod_is_fetched():
    """Cannot prove it is unchanged, so assume it changed."""
    to_fetch, _ = plan({"u": None}, {"u": FETCHED})
    assert to_fetch == {"u"}


def test_an_unknown_download_time_is_fetched():
    to_fetch, _ = plan({"u": LONG_BEFORE}, {"u": None})
    assert to_fetch == {"u"}


def test_urls_no_longer_listed_are_reported_not_fetched():
    to_fetch, retired = plan({"live": LONG_BEFORE}, {"live": FETCHED, "gone": FETCHED})
    assert to_fetch == set()
    assert retired == {"gone"}


def test_a_realistic_mixture():
    discovered = {
        "unchanged": LONG_BEFORE,
        "edited": AFTER,
        "new": LONG_BEFORE,
        "undated": None,
    }
    fetched = {
        "unchanged": FETCHED,
        "edited": FETCHED,
        "gone": FETCHED,
        "undated": FETCHED,
    }
    to_fetch, retired = plan(discovered, fetched)
    assert to_fetch == {"edited", "new", "undated"}
    assert retired == {"gone"}


# --------------------------------------------------------------------------
# Scrape: download pages, never parse
# --------------------------------------------------------------------------

A = "https://www.coffeereview.com/review/a/"
B = "https://www.coffeereview.com/review/b/"
C = "https://www.coffeereview.com/review/c/"


@pytest.fixture
def pages(tmp_path):
    return PageStore(tmp_path / "downloaded")


@pytest.fixture
def site(monkeypatch):
    """A fake site: discovery lists `listed`, and fetch serves `served`.

    A URL missing from `served` fails to fetch. Every fetch is recorded.
    """

    class Site:
        listed: dict = {}
        served: dict = {}
        fetched: list = []

    async def fake_discovery(client, semaphore):
        return dict(Site.listed)

    async def fake_fetch(url, client, semaphore, retries=5):
        Site.fetched.append(url)
        return Site.served.get(url)

    monkeypatch.setattr(scrape_module, "get_review_urls", fake_discovery)
    monkeypatch.setattr(scrape_module, "fetch", fake_fetch)
    Site.fetched = []
    return Site


# Edit times for the fake site, relative to the real clock of the test run.
OLD = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
LATER = datetime(2100, 1, 1, tzinfo=UTC)


def scrape(pages, **kwargs):
    return asyncio.run(
        scrape_all_reviews(pages, concurrency=2, lookback=LOOKBACK, **kwargs)
    )


def test_scrape_saves_each_page_with_its_lastmod_date(pages, site):
    site.listed = {A: OLD, B: None}
    site.served = {A: "<html>a</html>", B: "<html>b</html>"}

    result = scrape(pages)

    assert result.saved == 2
    manifest = pages.manifest()
    assert manifest[A].sitemap_lastmod == date(2026, 1, 1)  # stored as a date
    assert manifest[B].sitemap_lastmod is None
    assert pages.read(manifest[A]) == "<html>a</html>"


def test_a_failed_fetch_is_not_saved_and_the_rest_are(pages, site):
    site.listed = {A: OLD, B: OLD, C: OLD}
    site.served = {A: "a", C: "c"}  # B fails

    result = scrape(pages)

    assert result.failed == [B]
    assert set(pages.manifest()) == {A, C}


def test_an_exception_while_fetching_does_not_abort_the_run(pages, site, monkeypatch):
    site.listed = {A: OLD, B: OLD}

    async def exploding_fetch(url, client, semaphore, retries=5):
        if url == A:
            raise RuntimeError("unexpected")
        return "b"

    monkeypatch.setattr(scrape_module, "fetch", exploding_fetch)
    result = scrape(pages)
    assert result.failed == [A]
    assert set(pages.manifest()) == {B}


def test_a_second_run_fetches_only_what_changed(pages, site):
    site.listed = {A: OLD, B: OLD}
    site.served = {A: "a", B: "b"}
    scrape(pages)

    site.fetched = []
    site.listed = {A: OLD, B: LATER}  # B edited after we downloaded it
    scrape(pages)

    assert site.fetched == [B]


def test_full_refetches_everything(pages, site):
    site.listed = {A: OLD, B: OLD}
    site.served = {A: "a", B: "b"}
    scrape(pages)

    site.fetched = []
    scrape(pages, full=True)
    assert sorted(site.fetched) == [A, B]


def test_limit_caps_the_pages_fetched(pages, site):
    site.listed = {A: OLD, B: OLD, C: OLD}
    site.served = {A: "a", B: "b", C: "c"}
    assert scrape(pages, limit=2).saved == 2


def test_a_page_gone_from_the_sitemap_is_reported_and_kept(pages, site):
    site.listed = {A: OLD, B: OLD}
    site.served = {A: "a", B: "b"}
    scrape(pages)

    site.listed = {A: OLD}
    result = scrape(pages)
    assert result.retired == 1
    assert set(pages.manifest()) == {A, B}
