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

from coffee.parser import _parse_tables, clean_text, parse_html


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


# --------------------------------------------------------------------------
# Whitespace normalization
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("  leading and trailing  ", "leading and trailing"),
        ("double  spaces", "double spaces"),
        ("embedded\nnewline", "embedded newline"),
        ("tab\tseparated", "tab separated"),
        ("many \n\n  mixed\t\tkinds", "many mixed kinds"),
        ("\xa0non-breaking\xa0space\xa0", "non-breaking space"),
        ("already clean", "already clean"),
        ("", ""),
        ("   ", ""),
    ],
)
def test_clean_text(raw, expected):
    assert clean_text(raw) == expected


def test_extracted_fields_carry_no_internal_newlines():
    """The specific defect this normalization exists for.

    Review prose arrives from the HTML with newlines baked in. Left alone they
    survive into the CSV as multi-line quoted fields, where anything reading the
    file line-by-line instead of as CSV can reach inside a value and edit it.
    """
    html = """
    <h1 class="review-title">A   Coffee</h1>
    <h2>Blind Assessment</h2>
    <p>Sweetly nut-toned.
       Cantaloupe,  amber, bay leaf.
    </p>
    <table><tr><td>Roaster  Location:</td><td>Auburn,\n  Maine</td></tr></table>
    """
    data = parse_html(html)
    assert data["title"] == "A Coffee"
    assert data["blind_assessment"] == "Sweetly nut-toned. Cantaloupe, amber, bay leaf."
    # Table keys are normalized too, or the column name itself carries the noise.
    assert data["roaster location"] == "Auburn, Maine"

    for field, value in data.items():
        if isinstance(value, str):
            assert "\n" not in value, f"{field} kept a newline"
            assert "  " not in value, f"{field} kept a double space"
