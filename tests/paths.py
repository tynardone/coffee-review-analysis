"""Where the test inputs live, relative to the repository root."""

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures"
FIXTURE_HTML = FIXTURES / "html"
GOLDEN = FIXTURES / "parsed_reviews.json"


def review_pages() -> list[Path]:
    """Every saved review page, in a stable order."""
    return sorted(FIXTURE_HTML.glob("*.html"))
