"""Parse a CoffeeReview review page into a flat dict of fields.

:func:`parse_html` extracts the rating, roaster, title, blind assessment,
notes, and bottom line, then merges in the review's spec table (coffee origin,
price, agtron, etc.). Parsing is pure CPU work with no I/O, so the functions
are synchronous; run them in a thread (e.g. ``asyncio.to_thread``) to avoid
blocking the event loop during a scrape.

Field names are normalised at this boundary by :func:`normalise_field_name`, so
the raw layer lands as ``est_price`` rather than ``"Est. Price:"``. The scraped
label is presentation and the field name is schema; fixing the mapping here
means no downstream consumer re-derives it.
"""

import logging
import re

from bs4 import BeautifulSoup
from bs4.element import Tag

__all__ = [
    "normalise_field_name",
    "parse_html",
]


def normalise_field_name(label: str) -> str:
    """The site's table label -> the field name it is stored under.

    ``"Est. Price:"`` becomes ``"est_price"``. Applying this at parse time
    rather than downstream keeps a scraped label out of storage, so that each
    consumer reads the same field name instead of deriving its own.

    This is not a general slugifier. ``acidity/structure`` keeps its slash,
    since that is the name the site uses and the name the cleaning layer
    coalesces the field by; renaming it here would relocate the translation
    rather than remove it.
    """
    return label.strip().lower().replace(":", "").replace(" ", "_").replace(".", "")


def _parse_element(
    soup: BeautifulSoup,
    element: str,
    class_: str | None = None,
    string: str | None = None,
    next_element: str | None = None,
) -> str | None:
    # bs4's find() doesn't accept class_=None, so express the class filter via
    # attrs, using an empty (no-op) filter when no class is provided.
    found_element = soup.find(
        element,
        attrs={"class": class_} if class_ is not None else {},
        string=re.compile(string) if string else None,
    )
    if found_element:
        if next_element:
            found_next_element = found_element.find_next(next_element)
            if found_next_element:
                return found_next_element.get_text().strip()
        else:
            return found_element.get_text().strip()
    return None


def _parse_notes_section(soup: BeautifulSoup) -> str | None:
    """Text content between the Notes heading and the next h2 heading.

    The section's internal structure varies between pages, so the extent is
    defined by the surrounding headings rather than by the markup within.
    """
    notes = soup.find("h2", string=re.compile("Notes"))
    if notes:
        notes_text: str = ""
        # Accumulate text until the next h2 heading.
        for element in notes.find_next_siblings():
            # find_next_siblings() yields only Tags, but the type is narrowed
            # explicitly rather than asserted, since an assert is compiled out
            # under `python -O` and this runs against third-party markup.
            if not isinstance(element, Tag):
                continue
            if element.name == "h2":
                break
            notes_text += element.get_text().strip()
        return re.sub(r"\s+", " ", notes_text)
    else:
        logging.warning("No notes section found.")
        return None


def _parse_tables(soup: BeautifulSoup) -> dict[str, str]:
    """Extract two-column tables into a dict, keyed by normalised field name."""
    data: dict[str, str] = {}
    for table in soup.find_all("table"):
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) == 2:
                data[cells[0].get_text().strip()] = cells[1].get_text().strip()
    return {normalise_field_name(key): value for key, value in data.items()}


def parse_html(text: str) -> dict[str, str | None]:
    soup: BeautifulSoup = BeautifulSoup(text, "lxml")
    data: dict[str, str | None] = {
        "rating": _parse_element(soup, "span", "review-template-rating"),
        "roaster": _parse_element(soup, "p", "review-roaster"),
        "title": _parse_element(soup, "h1", "review-title"),
        "blind_assessment": _parse_element(
            soup, "h2", string="Blind Assessment", next_element="p"
        ),
        "notes": _parse_notes_section(soup),
        "bottom_line": _parse_element(
            soup, "h2", string="Bottom Line", next_element="p"
        ),
    }

    table_data = _parse_tables(soup)
    if table_data:
        data.update(table_data)

    return data
