"""Sitemap discovery tests.

The invariant throughout is that discovery is complete or loud: a short URL
list yields a dataset that looks fine and is quietly missing rows, so every
partial-failure path is asserted to raise rather than return what it got.
"""

import asyncio
from datetime import date

import pytest

from coffee import sitemap
from coffee.sitemap import (
    SitemapError,
    get_review_urls,
    is_review_url,
    parse_sitemap,
)

NS = 'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"'

INDEX = f"""<?xml version="1.0" encoding="UTF-8"?>
<sitemapindex {NS}>
  <sitemap><loc>https://x.test/review-sitemap.xml</loc></sitemap>
  <sitemap><loc>https://x.test/page-sitemap.xml</loc></sitemap>
</sitemapindex>"""

REVIEWS = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset {NS}>
  <url><loc>https://x.test/review/</loc><lastmod>2026-09-18T15:20:08+00:00</lastmod></url>
  <url><loc>https://x.test/review/alpha/</loc><lastmod>2026-09-18T15:20:08+00:00</lastmod></url>
  <url><loc>https://x.test/review/beta/</loc><lastmod>2014-10-02</lastmod></url>
  <url><loc>https://x.test/review/gamma/</loc></url>
</urlset>"""

PAGES = f"""<?xml version="1.0" encoding="UTF-8"?>
<urlset {NS}>
  <url><loc>https://x.test/about/</loc><lastmod>2026-01-01</lastmod></url>
</urlset>"""

DOCS = {
    "https://x.test/sitemap_index.xml": INDEX,
    "https://x.test/review-sitemap.xml": REVIEWS,
    "https://x.test/page-sitemap.xml": PAGES,
}


def discover(docs=DOCS, monkeypatch=None, **kwargs):
    async def fake_fetch(url, session, semaphore, retries=5):
        return docs.get(url)

    monkeypatch.setattr(sitemap, "fetch", fake_fetch)

    async def run():
        return await get_review_urls(
            session=None,
            semaphore=asyncio.Semaphore(4),
            index_url="https://x.test/sitemap_index.xml",
            **kwargs,
        )

    return asyncio.run(run())


# --------------------------------------------------------------------------
# Document parsing
# --------------------------------------------------------------------------


def test_index_yields_children_and_no_entries():
    children, entries = parse_sitemap(INDEX.encode())
    assert children == [
        "https://x.test/review-sitemap.xml",
        "https://x.test/page-sitemap.xml",
    ]
    assert entries == {}


def test_urlset_yields_entries_and_no_children():
    children, entries = parse_sitemap(REVIEWS.encode())
    assert children == []
    assert entries["https://x.test/review/alpha/"] == date(2026, 9, 18)


def test_lastmod_accepts_both_date_and_full_timestamp():
    _, entries = parse_sitemap(REVIEWS.encode())
    assert entries["https://x.test/review/beta/"] == date(2014, 10, 2)


def test_missing_lastmod_is_none_not_an_error():
    _, entries = parse_sitemap(REVIEWS.encode())
    assert entries["https://x.test/review/gamma/"] is None


def test_unparseable_lastmod_degrades_to_none():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset {NS}><url><loc>https://x.test/review/a/</loc>
    <lastmod>last thursday</lastmod></url></urlset>""".encode()
    _, entries = parse_sitemap(xml)
    assert entries["https://x.test/review/a/"] is None


def test_sitemap_without_a_namespace_still_parses():
    """local-name() lookups must not depend on the default namespace."""
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset><url><loc>https://x.test/review/a/</loc></url></urlset>"""
    _, entries = parse_sitemap(xml)
    assert "https://x.test/review/a/" in entries


def test_malformed_xml_raises_sitemap_error():
    with pytest.raises(SitemapError, match="Malformed"):
        parse_sitemap(b"<urlset><url><loc>oops")


def test_encoding_declaration_is_handled():
    """lxml refuses a str carrying an encoding declaration; bytes must work."""
    assert parse_sitemap(REVIEWS.encode())[1]


# --------------------------------------------------------------------------
# URL filtering
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://x.test/review/alpha/", True),
        ("https://x.test/review/", False),  # the listing page, not a review
        ("https://x.test/about/", False),
        ("https://x.test/reviews/alpha/", False),
        ("https://x.test/review/a/?utm=1", True),
    ],
)
def test_is_review_url(url, expected):
    assert is_review_url(url) is expected


# --------------------------------------------------------------------------
# End-to-end discovery
# --------------------------------------------------------------------------


def test_discovery_follows_the_index_and_filters_to_reviews(monkeypatch):
    found = discover(monkeypatch=monkeypatch)
    assert set(found) == {
        "https://x.test/review/alpha/",
        "https://x.test/review/beta/",
        "https://x.test/review/gamma/",
    }
    assert found["https://x.test/review/alpha/"] == date(2026, 9, 18)


def test_non_review_sitemaps_are_fetched_but_their_urls_dropped(monkeypatch):
    """Filtering by URL path, not by sitemap filename, is what makes discovery
    survive the site renaming or resharding its sitemap files."""
    found = discover(monkeypatch=monkeypatch)
    assert not any("/about/" in url for url in found)


def test_a_failed_child_sitemap_raises_rather_than_returning_a_short_list(monkeypatch):
    """THE REGRESSION THAT MATTERS: silent partial discovery."""
    docs = dict(DOCS)
    docs["https://x.test/review-sitemap.xml"] = None
    with pytest.raises(SitemapError, match="Could not fetch"):
        discover(docs=docs, monkeypatch=monkeypatch)


def test_a_failed_index_raises(monkeypatch):
    with pytest.raises(SitemapError, match="Could not fetch"):
        discover(docs={}, monkeypatch=monkeypatch)


def test_finding_no_reviews_raises(monkeypatch):
    docs = dict(DOCS)
    docs["https://x.test/review-sitemap.xml"] = PAGES
    with pytest.raises(SitemapError, match="No review URLs"):
        discover(docs=docs, monkeypatch=monkeypatch)


def test_nested_indexes_are_followed(monkeypatch):
    docs = {
        "https://x.test/sitemap_index.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
            <sitemapindex {NS}><sitemap><loc>https://x.test/inner.xml</loc></sitemap>
            </sitemapindex>""",
        "https://x.test/inner.xml": INDEX,
        "https://x.test/review-sitemap.xml": REVIEWS,
        "https://x.test/page-sitemap.xml": PAGES,
    }
    assert len(discover(docs=docs, monkeypatch=monkeypatch)) == 3


def test_self_referential_index_terminates(monkeypatch):
    """A sitemap that points at itself must not loop forever."""
    docs = {
        "https://x.test/sitemap_index.xml": f"""<?xml version="1.0" encoding="UTF-8"?>
            <sitemapindex {NS}>
              <sitemap><loc>https://x.test/sitemap_index.xml</loc></sitemap>
              <sitemap><loc>https://x.test/review-sitemap.xml</loc></sitemap>
            </sitemapindex>""",
        "https://x.test/review-sitemap.xml": REVIEWS,
    }
    assert len(discover(docs=docs, monkeypatch=monkeypatch)) == 3
