"""Discover every coffee review URL from the site's XML sitemaps.

:func:`get_review_urls` reads ``sitemap_index.xml``, fetches each child sitemap
it names, and returns every review URL mapped to its ``<lastmod>`` date.

WHY THE SITEMAP AND NOT THE LISTING PAGES
    This module previously crawled ``/review/page/N`` breadth-first. The sitemap
    is better on all three axes that matter:

    COVERAGE. The sitemap is a strict superset of what pagination found —
        9,334 review URLs versus 9,054 scraped, with none going the other way.
        Pagination silently missed 280 reviews, including green-coffee reviews
        that never appear in the ``/review/`` listing at all.

    COST. Ten or so sitemap requests enumerate the entire corpus, against
        hundreds of listing-page fetches for the same information.

    CHANGE DETECTION. Every entry carries ``<lastmod>``, so a caller can fetch
        only what changed since its last run instead of re-scraping everything.
        That is what makes an unattended, scheduled scrape affordable and
        polite: ~200 pages a month rather than ~9,300.

FAILING LOUDLY
    Discovery failures raise :class:`SitemapError` rather than returning a short
    list. A partial URL set is the dangerous failure here — it produces a
    plausible-looking dataset that is quietly missing rows, which is exactly the
    kind of error nobody notices downstream.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from urllib.parse import urlparse

import aiohttp
from lxml import etree

from coffee.config import Config
from coffee.fetch import fetch

logger = logging.getLogger(__name__)

# Sitemaps are third-party XML: don't resolve entities and don't let the parser
# reach the network while doing it.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)

# Guards against a malformed index that points at itself, directly or in a loop.
MAX_SITEMAP_DEPTH = 5


class SitemapError(RuntimeError):
    """Discovery could not complete, so the URL set would be incomplete."""


def _parse_lastmod(value: str | None) -> date | None:
    """``2026-09-18T15:20:08+00:00`` -> ``date(2026, 9, 18)``; None if unusable."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        logger.warning("Unparseable <lastmod> %r", value)
        return None


def parse_sitemap(xml: bytes) -> tuple[list[str], dict[str, date | None]]:
    """Split one sitemap document into (child sitemaps, {page URL: lastmod}).

    Handles both document kinds with one pass, since an index (``<sitemapindex>``
    of ``<sitemap>``) and a leaf (``<urlset>`` of ``<url>``) differ only in which
    elements they contain, and the spec permits either at any level.

    Element lookups go through ``local-name()`` so a sitemap that omits the
    default namespace — or declares a different one — still parses.

    Takes bytes, not str: every sitemap here carries an ``<?xml ... encoding?>``
    declaration, and lxml refuses to parse those from a str.
    """
    try:
        root = etree.fromstring(xml, parser=_PARSER)
    except etree.XMLSyntaxError as exc:
        raise SitemapError(f"Malformed sitemap XML: {exc}") from exc

    children = [
        loc.strip()
        for loc in root.xpath(
            "//*[local-name()='sitemap']/*[local-name()='loc']/text()"
        )
    ]

    entries: dict[str, date | None] = {}
    for url_element in root.xpath("//*[local-name()='url']"):
        loc = url_element.xpath("./*[local-name()='loc']/text()")
        if not loc:
            continue
        lastmod = url_element.xpath("./*[local-name()='lastmod']/text()")
        raw = lastmod[0].strip() if lastmod else None
        entries[loc[0].strip()] = _parse_lastmod(raw)

    return children, entries


def is_review_url(url: str, path_prefix: str = "/review/") -> bool:
    """True for an individual review page.

    The length check excludes the listing page ``/review/`` itself, which the
    sitemap lists alongside the reviews and which is not a review.
    """
    path = urlparse(url).path
    return path.startswith(path_prefix) and len(path) > len(path_prefix)


async def get_review_urls(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    index_url: str = Config.SITEMAP_URL,
    path_prefix: str = "/review/",
) -> dict[str, date | None]:
    """Return every review URL mapped to its sitemap ``<lastmod>`` date.

    Fetches the index, then every sitemap it names — including the non-review
    ones. Filtering the *URLs* by path rather than guessing from *sitemap
    filenames* costs a handful of extra requests per run and buys immunity to
    the site renaming or resharding its sitemap files, which would otherwise
    shrink the corpus silently.
    """
    logger.info("Discovering review URLs from %s", index_url)

    pending = [index_url]
    visited: set[str] = set()
    all_entries: dict[str, date | None] = {}

    for _ in range(MAX_SITEMAP_DEPTH):
        frontier = [url for url in pending if url not in visited]
        if not frontier:
            break
        visited.update(frontier)

        documents = await asyncio.gather(
            *(fetch(url, session, semaphore) for url in frontier)
        )

        failed = [
            url for url, doc in zip(frontier, documents, strict=True) if doc is None
        ]
        if failed:
            raise SitemapError(
                f"Could not fetch {len(failed)} of {len(frontier)} sitemap(s): "
                f"{', '.join(failed[:3])}"
                f"{'...' if len(failed) > 3 else ''}"
            )

        pending = []
        for document in documents:
            assert document is not None  # narrowed by the `failed` check above
            children, entries = parse_sitemap(document.encode("utf-8"))
            pending.extend(children)
            all_entries.update(entries)
    else:
        raise SitemapError(f"Sitemap nesting exceeded {MAX_SITEMAP_DEPTH} levels")

    reviews = {
        url: lastmod
        for url, lastmod in all_entries.items()
        if is_review_url(url, path_prefix)
    }
    if not reviews:
        raise SitemapError(
            f"No review URLs found under {path_prefix!r} in {len(visited)} sitemap(s)"
        )

    logger.info(
        "Discovered %d review URLs from %d sitemaps (%d non-review URLs ignored)",
        len(reviews),
        len(visited),
        len(all_entries) - len(reviews),
    )
    return reviews
