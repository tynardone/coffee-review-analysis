import logging
import re
from pathlib import Path
from bs4 import BeautifulSoup

# Function parse_html for parsing review data from HTML scraped in async_scrape_roast_reviews.py
# In main function test html is loaded from tests/html/ and parsed using function.
# parse_html imported into async_scrape_roast_reviews.py

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.WARNING, format="%(asctime)s - %(levelname)s - %(message)s"
)

current_file = Path(__file__)
root_dir = current_file.parent.parent
html_dir = root_dir / "tests" / "html"


def _parse_tables(soup: BeautifulSoup) -> dict:
    data = {}
    tables = soup.find_all("table")
    for table in tables:
        for row in table.find_all("tr"):
            cells = row.find_all("td")
            if len(cells) == 2:
                data[cells[0].string.strip()] = cells[1].string.strip()
    logger.info("Parsed %s table rows.", len(data))
    return data


def _parse_class(soup: BeautifulSoup, element: str, class_: str) -> str:
    found_element = soup.find(element, class_=class_)
    if found_element:
        return found_element.string.strip()
    logger.warning("No data found for %s with class %s.", found_element, class_)
    return None


def _parse_string_next(
    soup: BeautifulSoup,
    find_element: str,
    next_element: str,
    string: str,
) -> str:
    element = soup.find(find_element, string=re.compile(string))
    if element:
        found_next_element = element.find_next(next_element)
        if found_next_element:
            return found_next_element.get_text().strip()
    return None


def _parse_notes_section(soup: BeautifulSoup) -> str:
    notes = soup.find("h2", string=re.compile("Notes"))
    # Get text from every element after notes element until the next h2 element
    if notes:
        notes_text = ""
        for element in notes.find_next_siblings():
            if element.name == "h2":
                break
            notes_text += element.get_text().strip()
            notes_text_cleaned = re.sub(r"\s+", " ", notes_text)
        return notes_text_cleaned
    return None


def parse_html(html: str) -> dict:
    data = {}
    soup = BeautifulSoup(html, "html.parser")

    rating = _parse_class(soup, "span", "review_template_rating")
    roaster = _parse_class(soup, "p", "review_roaster")
    title = _parse_class(soup, "h1", "review_title")
    blind_assessment = _parse_string_next(soup, "h2", "p", "Blind Assessment")
    notes = _parse_notes_section(soup)
    # Older reviews do NOT have a bottom line
    bottom_line = _parse_string_next(soup, "h2", "p", "Bottom Line")
    table_data = _parse_tables(soup)
    data["rating"] = rating
    data["roaster"] = roaster
    data["title"] = title
    data["blind assessment"] = blind_assessment
    data["notes"] = notes
    data["bottom line"] = bottom_line
    data.update(table_data)
    n_fields = len(data)
    logging.info("Parsed %s data fields for %s - %s.", n_fields, roaster, title)
    return data


if __name__ == "__main__":
    html_files = list(html_dir.glob("*.html"))
    for file in html_files:
        with open(file, "r", encoding="utf-8") as f:
            html = f.read()
            data = parse_html(html)
            print(data)
