"""Contains logic for parsing coffee review HTML."""

from bs4 import BeautifulSoup
import re
import logging


async def _parse_element(
    soup: BeautifulSoup,
    element: str,
    class_: str,
    string: str,
    next_element: str | None = None,
) -> str | None:
    found_element = soup.find(
        element, class_=class_, string=re.compile(string) if string else None
    )
    if found_element:
        if next_element:
            found_next_element = found_element.find_next(next_element)
            if found_next_element:
                return found_next_element.get_text().strip()
        else:
            return found_element.get_text().strip()
    return None


async def _parse_notes_section(soup: BeautifulSoup) -> str | None:
    notes = soup.find("h2", string=re.compile("Notes"))
    if notes:
        notes_text: str = ""
        for element in notes.find_next_siblings():
            if element.name == "h2":
                break
            notes_text += element.get_text().strip()
        return re.sub(r"\s+", " ", notes_text)
    else:
        logging.warning("No notes section found.")
        return None


async def _parse_tables(soup: BeautifulSoup) -> dict:
    data: dict = {}
    for table in soup.find_all("table"):
        if table:
            for row in table.find_all("tr"):
                cells = row.find_all("td")
                if len(cells) == 2:
                    data[cells[0].get_text().strip()] = cells[1].get_text().strip()
        else:
            logging.warning("No tables found.")
            return None
    # Clean up keys
    data = {key.lower().replace(":", ""): value for key, value in data.items()}
    return data


async def parse_html(text):
    soup: BeautifulSoup = BeautifulSoup(text, "html.parser")
    data: dict[str, str | None] = {
        "rating": await _parse_element(soup, "span", "review-template-rating"),
        "roaster": await _parse_element(soup, "p", "review-roaster"),
        "title": await _parse_element(soup, "h1", "review-title"),
        "blind_assessment": await _parse_element(
            soup, "h2", string="Blind Assessment", next_element="p"
        ),
        "notes": await _parse_notes_section(soup),
        "bottom_line": await _parse_element(
            soup, "h2", string="Bottom Line", next_element="p"
        ),
    }

    data.update(await _parse_tables(soup))

    return data
