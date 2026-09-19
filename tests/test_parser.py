"""Golden-output tests for the review parser.

WHY A GOLDEN FILE RATHER THAN SPOT ASSERTIONS
    Parser regressions here are SILENT. When CoffeeReview changes a class name,
    `_parse_element` returns None and you get a column of nulls — not an error,
    not a crash, just slightly emptier data that looks plausible. There is no
    way to distinguish "the site changed" from "the scraper broke" after the
    fact, because real schema evolution produces the identical symptom: the
    `bottom_line` field is genuinely absent from every review before mid-2016,
    and `acidity` was genuinely renamed `acidity/structure` across 2017-18.

    Pinning the full parse of ten real pages turns that silence into a failing
    test. Regenerate with `python tests/generate_golden.py` after a deliberate
    parser change — and read the diff.
"""

import pytest
from bs4 import BeautifulSoup
from conftest import review_pages

from coffee.parser import _parse_tables, parse_html


@pytest.mark.parametrize("path", review_pages(), ids=lambda p: p.stem)
def test_parse_matches_golden(path, golden):
    assert path.name in golden, (
        f"{path.name} has no golden entry; run tests/generate_golden.py"
    )
    assert parse_html(path.read_text(encoding="utf-8")) == golden[path.name]


@pytest.mark.parametrize("path", review_pages(), ids=lambda p: p.stem)
def test_core_fields_are_always_present(path):
    """The fields that must never be null, regardless of the page's vintage.

    Distinct from the golden test: this one states the INVARIANT rather than the
    current output, so it stays meaningful when the golden file is regenerated.
    """
    data = parse_html(path.read_text(encoding="utf-8"))
    for field in ("rating", "roaster", "title", "blind_assessment", "review date"):
        assert data.get(field), f"{field} missing from {path.name}"


def test_parse_html_on_unrecognized_markup_returns_nulls_not_errors():
    """An unexpected page yields empty fields rather than raising.

    The scraper depends on this: a page that raises would be a scrape-wide
    failure, whereas None fields are merely a row to drop.
    """
    data = parse_html("<html><body><p>nothing to see</p></body></html>")
    assert data["rating"] is None
    assert data["title"] is None


def test_table_keys_are_lowercased_and_colon_free():
    html = """
    <table><tr><td>Roast Level:</td><td>Medium-Light</td></tr>
           <tr><td>Agtron:</td><td>57/80</td></tr></table>
    """
    assert _parse_tables(BeautifulSoup(html, "lxml")) == {
        "roast level": "Medium-Light",
        "agtron": "57/80",
    }


def test_notes_section_stops_at_the_next_heading():
    """Notes run from the Notes heading to the next h2 — not past it."""
    html = """
    <h2>Notes</h2><p>First paragraph.</p><p>Second paragraph.</p>
    <h2>Bottom Line</h2><p>Should not appear in notes.</p>
    """
    notes = parse_html(html)["notes"]
    assert notes is not None
    assert "First paragraph." in notes
    assert "Second paragraph." in notes
    assert "Should not appear" not in notes
