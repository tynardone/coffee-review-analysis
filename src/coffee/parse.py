"""Step 2, ``parse-reviews``: turn saved review pages into ``reviews.csv``.

:func:`parse_saved_reviews` reads pages saved by :mod:`coffee.scrape`, turns
each into fields with :mod:`coffee.review_page`, and writes the rows through
:mod:`coffee.review_store`. It never touches the network, so after a parser
change the whole corpus is re-parsed from saved pages in about a minute rather
than downloaded again.

A page that cannot be parsed keeps its previous row. The rest of the run
carries on, and the failure is reported.
"""

import logging
from dataclasses import dataclass, field

from tqdm import tqdm

from coffee.page_store import PageStore
from coffee.review_page import parse_html
from coffee.review_store import (
    SCRAPED_AT_COLUMN,
    SITEMAP_LASTMOD_COLUMN,
    URL_COLUMN,
    ReviewStore,
)

__all__ = ["ParseResult", "parse_saved_reviews"]

logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    parsed: int = 0
    failed: list[str] = field(default_factory=list)
    total: int = 0  # reviews held afterwards


def parse_saved_reviews(
    pages: PageStore,
    store: ReviewStore,
    *,
    full: bool = False,
    limit: int | None = None,
) -> ParseResult:
    """Parse saved pages into the review store.

    By default only pages the store has not yet parsed are read: those it has
    no row for, or whose saved copy was fetched after the row was written.
    ``full`` re-parses every saved page, which is what a parser change needs.
    ``limit`` caps how many pages are parsed.

    Rows are added or replaced by URL, never removed.
    """
    saved = pages.manifest()
    if full:
        todo = list(saved.values())
    else:
        parsed_from = store.parsed_from()
        todo = [
            page
            for page in saved.values()
            if parsed_from.get(page.url) != page.fetched_at
        ]
    todo.sort(key=lambda page: page.url)
    if limit is not None:
        todo = todo[:limit]
    logger.info("%d saved pages; parsing %d", len(saved), len(todo))

    result = ParseResult()
    records = []
    for page in tqdm(todo):
        try:
            fields = parse_html(pages.read(page))
        except Exception:
            # One malformed page must not cost the rest of the run. Its old
            # row, if any, stays as it was.
            logger.exception("Failed to parse %s", page.url)
            result.failed.append(page.url)
            continue
        records.append(
            {
                **fields,
                URL_COLUMN: page.url,
                SITEMAP_LASTMOD_COLUMN: page.sitemap_lastmod,
                SCRAPED_AT_COLUMN: page.fetched_at,
            }
        )

    result.parsed = len(records)
    result.total = store.upsert(records)
    return result
