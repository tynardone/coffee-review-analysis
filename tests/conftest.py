"""Shared test fixtures and path setup.

`scripts/` is deliberately not a package (it holds runnable pipeline steps, not
importable library code), so tests that exercise a script import it by putting
`scripts/` on the path here rather than reaching for a relative import.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_HTML = Path(__file__).resolve().parent / "fixtures" / "html"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "parsed_reviews.json"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))


def review_pages() -> list[Path]:
    """Every saved review page, in a stable order."""
    return sorted(FIXTURE_HTML.glob("*.html"))


@pytest.fixture(scope="session")
def golden() -> dict:
    """The expected parse of every fixture page, keyed by filename."""
    import json

    return json.loads(GOLDEN.read_text(encoding="utf-8"))
