"""Discover every coffee review URL from the site's XML sitemaps.

:func:`get_review_urls` reads ``sitemap_index.xml``, fetches each child sitemap
it names, and returns every review URL mapped to its ``<lastmod>`` date. That
date lets a caller re-fetch only what changed rather than the whole corpus.

Discovery raises :class:`SitemapError` rather than returning a partial list,
since a short URL set produces a dataset that appears complete while missing
rows.
"""

import asyncio
import logging
from datetime import date, datetime
from urllib.parse import urlparse

import aiohttp
from lxml import etree

from coffee.config import SITEMAP_URL
from coffee.fetch import fetch

__all__ = [
    "MAX_SITEMAP_DEPTH",
    "SitemapError",
    "get_review_urls",
    "is_review_url",
    "parse_sitemap",
]

logger = logging.getLogger(__name__)

# Sitemaps are third-party XML, so entity resolution and network access are
# both disabled.
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

    Indexes and leaf urlsets are handled in one pass, since the spec permits
    either at any level. ``local-name()`` lookups keep this working when a
    sitemap omits or changes the default namespace.

    Takes bytes rather than str, because lxml refuses to parse a str carrying
    an ``<?xml ... encoding?>`` declaration and every sitemap here has one.
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

    The length check excludes the ``/review/`` listing page, which the sitemap
    lists alongside the reviews.
    """
    path = urlparse(url).path
    return path.startswith(path_prefix) and len(path) > len(path_prefix)


async def get_review_urls(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    index_url: str = SITEMAP_URL,
    path_prefix: str = "/review/",
) -> dict[str, date | None]:
    """Return every review URL mapped to its sitemap ``<lastmod>`` date.

    Fetches every sitemap the index names, including non-review ones, and
    filters the resulting URLs by path. Filtering by URL rather than guessing
    from sitemap filenames costs a few extra requests and continues to work if
    the site renames or reshards its sitemap files.
    """
    logger.info("Discovering review URLs from %s", index_url)

    frontier = [index_url]
    visited: set[str] = set()
    all_entries: dict[str, date | None] = {}
    depth = 0

    while frontier:
        depth += 1
        if depth > MAX_SITEMAP_DEPTH:
            raise SitemapError(f"Sitemap nesting exceeded {MAX_SITEMAP_DEPTH} levels")
        visited.update(frontier)

        documents = await asyncio.gather(
            *(fetch(url, session, semaphore) for url in frontier)
        )

        failed = [
            url for url, doc in zip(frontier, documents, strict=True) if doc is None
        ]
        if failed:
            shown = ", ".join(failed[:3]) + ("..." if len(failed) > 3 else "")
            raise SitemapError(
                f"Could not fetch {len(failed)} of {len(frontier)} sitemap(s): {shown}"
            )

        children: list[str] = []
        for document in documents:
            if document is None:  # ruled out above; narrows the type for mypy
                continue
            child_urls, entries = parse_sitemap(document.encode("utf-8"))
            children.extend(child_urls)
            all_entries.update(entries)

        frontier = [url for url in dict.fromkeys(children) if url not in visited]

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
