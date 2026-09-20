"""Golden-output tests for the review parser.

Parser regressions are silent: a changed class name makes `_parse_element`
return None, so you get a column of nulls rather than an error. That is
indistinguishable from real schema evolution, which this corpus has —
`bottom_line` is absent before mid-2016, and `acidity` became
`acidity/structure` across 2017-18. Pinning the full parse of ten real pages
turns that silence into a failing test.

Regenerate with `python tests/generate_golden.py` after a deliberate parser
change, and read the diff.
"""

import pytest
from bs4 import BeautifulSoup
from conftest import review_pages

from coffee.parser import _parse_tables, normalise_field_name, parse_html


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
    for field in ("rating", "roaster", "title", "blind_assessment", "review_date"):
        assert data.get(field), f"{field} missing from {path.name}"


def test_parse_html_on_unrecognized_markup_returns_nulls_not_errors():
    """An unexpected page yields empty fields rather than raising.

    The scraper depends on this: a page that raises would be a scrape-wide
    failure, whereas None fields are merely a row to drop.
    """
    data = parse_html("<html><body><p>nothing to see</p></body></html>")
    assert data["rating"] is None
    assert data["title"] is None


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Est. Price:", "est_price"),
        (" Roaster Location ", "roaster_location"),
        ("AGTRON", "agtron"),
        ("With Milk:", "with_milk"),
        # Kept as-is: the cleaning layer coalesces this field BY THIS NAME, so
        # prettifying the slash here would only move the translation elsewhere.
        ("Acidity/Structure:", "acidity/structure"),
        # Already normalised: the rule has to be idempotent, because
        # check_raw_schema uses it to decide whether a file needs migrating.
        ("est_price", "est_price"),
    ],
)
def test_normalise_field_name(label, expected):
    assert normalise_field_name(label) == expected
    assert normalise_field_name(expected) == expected


def test_table_keys_are_normalised_to_field_names():
    html = """
    <table><tr><td>Roast Level:</td><td>Medium-Light</td></tr>
           <tr><td>Agtron:</td><td>57/80</td></tr></table>
    """
    assert _parse_tables(BeautifulSoup(html, "lxml")) == {
        "roast_level": "Medium-Light",
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


# --------------------------------------------------------------------------
# Whitespace
# --------------------------------------------------------------------------


def test_extracted_fields_are_stripped_but_prose_is_left_alone():
    """Leading/trailing whitespace goes; whatever is INSIDE a value stays.

    The markup indents its content, so every extracted value would otherwise
    carry the surrounding layout. Stripping the ends is lossless. Collapsing
    runs *within* a value is not — that edits the review prose itself — so the
    internal double space and newline below must survive verbatim.
    """
    html = """
    <h1 class="review-title">
        A Coffee
    </h1>
    <h2>Blind Assessment</h2>
    <p>
        Sweetly nut-toned.  Cantaloupe,
        amber, bay leaf.
    </p>
    <table><tr><td>  Roast Level:  </td><td>  Medium-Light  </td></tr></table>
    """
    data = parse_html(html)

    assert data["title"] == "A Coffee"
    assert data["blind_assessment"].startswith("Sweetly")
    assert data["blind_assessment"].endswith("bay leaf.")
    # Internal whitespace is content, not formatting: leave it as scraped.
    assert "  " in data["blind_assessment"]
    assert "\n" in data["blind_assessment"]

    # Table keys are stripped too, or the whitespace ends up in a column name.
    assert data["roast_level"] == "Medium-Light"


@pytest.mark.parametrize("path", review_pages(), ids=lambda p: p.stem)
def test_no_fixture_field_has_leading_or_trailing_whitespace(path):
    data = parse_html(path.read_text(encoding="utf-8"))
    for field, value in data.items():
        if isinstance(value, str):
            assert value == value.strip(), f"{field} in {path.name} is not stripped"
