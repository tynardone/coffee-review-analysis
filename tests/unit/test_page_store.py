"""Tests for the bronze page store: save, read back, and the manifest."""

import gzip
from datetime import date

from coffee.page_store import PageStore, _filename

URL = "https://www.coffeereview.com/review/some-coffee/"
JAN = date(2026, 1, 1)
JUN = date(2026, 6, 1)


def test_an_empty_store_has_no_pages(tmp_path):
    assert PageStore(tmp_path).manifest() == {}


def test_a_saved_page_reads_back_unchanged(tmp_path):
    pages = PageStore(tmp_path)
    html = "<html><h1>Café ☕</h1></html>"
    saved = pages.save(URL, html, JAN, fetched_at="2026-09-27T12:00:00+00:00")

    assert pages.read(saved) == html
    assert pages.manifest()[URL] == saved


def test_pages_are_stored_gzipped_under_a_readable_name(tmp_path):
    pages = PageStore(tmp_path)
    pages.save(URL, "<html/>", JAN, fetched_at="t")

    path = tmp_path / "pages" / "some-coffee.html.gz"
    assert gzip.decompress(path.read_bytes()) == b"<html/>"


def test_resaving_a_url_replaces_the_page_and_the_latest_entry_wins(tmp_path):
    pages = PageStore(tmp_path)
    pages.save(URL, "old", JAN, fetched_at="t1")
    latest = pages.save(URL, "new", JUN, fetched_at="t2")

    assert pages.manifest() == {URL: latest}
    assert pages.read(latest) == "new"
    assert pages.known_lastmods() == {URL: JUN}


def test_an_unknown_lastmod_round_trips_as_none(tmp_path):
    pages = PageStore(tmp_path)
    pages.save(URL, "x", None, fetched_at="t")
    assert pages.known_lastmods() == {URL: None}


def test_a_partial_last_manifest_line_is_skipped(tmp_path):
    """A run killed mid-write must not make the manifest unreadable."""
    pages = PageStore(tmp_path)
    pages.save(URL, "x", JAN, fetched_at="t")
    with pages.manifest_path.open("a") as manifest:
        manifest.write('{"url": "https://www.coffeereview.com/re')

    assert set(pages.manifest()) == {URL}


def test_an_odd_url_gets_a_hashed_name_that_stays_in_the_pages_dir():
    name = _filename("https://www.coffeereview.com/review/../../etc/passwd")
    assert "/" not in name and ".." not in name
    assert name.endswith(".html.gz")
