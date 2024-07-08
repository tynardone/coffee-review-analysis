import re
from bs4 import BeautifulSoup
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _parse_element(soup: BeautifulSoup, element: str, class_: str = None,
                   string: str = None, next_element: str = None) -> str:
    found_element = soup.find(
        element,
        class_=class_,
        string=re.compile(string) if string else None)
    if found_element:
        if next_element:
            found_next_element = found_element.find_next(next_element)
            if found_next_element:
                return found_next_element.get_text().strip()
        else:
            return found_element.get_text().strip()
    logging.warning(
        f"No data found for {element} with class {class_ or string}.")
    return None


def _parse_notes_section(soup: BeautifulSoup) -> str:
    notes = soup.find('h2', string=re.compile('Notes'))
    if notes:
        notes_text = ''
        for element in notes.find_next_siblings():
            if element.name == 'h2':
                break
            notes_text += element.get_text().strip()
        return re.sub(r'\s+', ' ', notes_text)
    logging.warning("No notes section found.")
    return None


def _parse_tables(soup: BeautifulSoup) -> dict[str, str]:
    data = {}
    for table in soup.find_all('table'):
        if table:
            for row in table.find_all('tr'):
                cells = row.find_all('td')
                if len(cells) == 2:
                    data[cells[0].get_text().strip()] = cells[1].get_text().strip()
        else:
            logging.warning("No tables found.")
            return None
    # lowercase keys
    # drop ':' from keys
    data = {key.lower().replace(':', ''): value for key, value in data.items()}
    return data


def parse_review(text: str) -> dict[str, str]:
    soup = BeautifulSoup(text, 'html.parser')
    data = {
        'rating': _parse_element(soup, 'span', 'review-template-rating'),
        'roaster': _parse_element(soup, 'p', 'review-roaster'),
        'title': _parse_element(soup, 'h1', 'review-title'),
        'blind_assessment': _parse_element(soup, 'h2',
                                           string='Blind Assessment',
                                           next_element='p'),
        'notes': _parse_notes_section(soup),
        'bottom_line': _parse_element(soup, 'h2',
                                      string='Bottom Line',
                                      next_element='p')
    }

    data.update(_parse_tables(soup))

    return data


def main():
    # for file in src/test_html/*.html
    root_dir = Path(__file__).parent.parent
    html_dir = root_dir / 'src' / 'test_html'
    html_files = html_dir.glob('*.html')
    for html_file in html_files:
        with open(html_file, 'r') as file:
            html = file.read()
            soup = BeautifulSoup(html, 'html.parser')
            try:
                data = parse_review(soup)
                print(data)
            except ValueError as e:
                logger.error("Error parsing %s: %s", html_file, e)


if __name__ == '__main__':
    main()
