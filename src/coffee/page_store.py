"""Downloaded review pages, exactly as the site served them.

The scrape saves each page here, gzipped, and the parse reads it back. Keeping
the HTML means a parser fix is a re-parse of saved pages, which takes about a
minute, rather than a re-download of the whole site, which takes half an hour.

Layout::

    data/downloaded/
        pages/<slug>.html.gz    one file per review
        manifest.jsonl          one line per saved page

The manifest is append-only. Each save adds a line recording the page's URL,
file, sitemap ``<lastmod>`` and fetch time; when a URL appears more than once,
the last line wins. A page that changes upstream overwrites its saved copy, so
the folder holds the latest version of each review, not its history.
"""

import gzip
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

__all__ = ["PageStore", "SavedPage"]

logger = logging.getLogger(__name__)

_SAFE_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,150}")


@dataclass(frozen=True)
class SavedPage:
    """One saved page, as recorded in the manifest."""

    url: str
    file: str  # relative to the pages directory
    sitemap_lastmod: date | None
    fetched_at: str  # ISO 8601, UTC


def _filename(url: str) -> str:
    """A readable, filesystem-safe file name for a review URL.

    ``https://www.coffeereview.com/review/some-coffee/`` becomes
    ``some-coffee.html.gz``. A URL whose path does not reduce to a safe slug
    gets a hash instead, so no URL can escape the pages directory.
    """
    path = urlparse(url).path.strip("/")
    slug = path.removeprefix("review/").replace("/", "__")
    if not _SAFE_SLUG.fullmatch(slug):
        slug = hashlib.sha1(url.encode()).hexdigest()
    return f"{slug}.html.gz"


class PageStore:
    """Saved review pages and the manifest that describes them."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.pages_dir = directory / "pages"
        self.manifest_path = directory / "manifest.jsonl"

    # -- reading -----------------------------------------------------------

    def manifest(self) -> dict[str, SavedPage]:
        """The latest saved page for every URL, keyed by URL."""
        if not self.manifest_path.exists():
            return {}
        pages: dict[str, SavedPage] = {}
        lines = self.manifest_path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines, start=1):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                # A run killed mid-write can leave a partial last line. Its
                # page is simply fetched again on the next run.
                logger.warning("Skipping unreadable manifest line %d", number)
                continue
            lastmod = entry["sitemap_lastmod"]
            pages[entry["url"]] = SavedPage(
                url=entry["url"],
                file=entry["file"],
                sitemap_lastmod=date.fromisoformat(lastmod) if lastmod else None,
                fetched_at=entry["fetched_at"],
            )
        return pages

    def fetch_times(self) -> dict[str, datetime | None]:
        """Every saved URL mapped to when its page was downloaded.

        None where the recorded time cannot be read, so the page is treated as
        unknown and fetched again rather than trusted.
        """
        times: dict[str, datetime | None] = {}
        for url, page in self.manifest().items():
            try:
                times[url] = datetime.fromisoformat(page.fetched_at)
            except ValueError:
                times[url] = None
        return times

    def read(self, page: SavedPage) -> str:
        """The HTML of a saved page."""
        return gzip.decompress((self.pages_dir / page.file).read_bytes()).decode(
            "utf-8"
        )

    # -- writing -----------------------------------------------------------

    def save(
        self, url: str, html: str, sitemap_lastmod: date | None, fetched_at: str
    ) -> SavedPage:
        """Write a page, then record it in the manifest.

        The file is written first and swapped into place, so the manifest never
        points at a half-written page. If the run dies between the two steps,
        the page is unrecorded and is fetched again next time.
        """
        page = SavedPage(url, _filename(url), sitemap_lastmod, fetched_at)
        self.pages_dir.mkdir(parents=True, exist_ok=True)
        target = self.pages_dir / page.file
        partial = target.with_name(target.name + ".partial")
        partial.write_bytes(gzip.compress(html.encode("utf-8")))
        os.replace(partial, target)

        entry = {
            "url": url,
            "file": page.file,
            "sitemap_lastmod": sitemap_lastmod.isoformat() if sitemap_lastmod else None,
            "fetched_at": fetched_at,
        }
        with self.manifest_path.open("a", encoding="utf-8") as manifest:
            manifest.write(json.dumps(entry) + "\n")
        return page
