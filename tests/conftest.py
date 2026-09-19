"""Shared test fixtures."""

from pathlib import Path

import pytest

FIXTURE_HTML = Path(__file__).resolve().parent / "fixtures" / "html"
GOLDEN = Path(__file__).resolve().parent / "fixtures" / "parsed_reviews.json"


def review_pages() -> list[Path]:
    """Every saved review page, in a stable order."""
    return sorted(FIXTURE_HTML.glob("*.html"))


@pytest.fixture(scope="session")
def golden() -> dict:
    """The expected parse of every fixture page, keyed by filename."""
    import json

    return json.loads(GOLDEN.read_text(encoding="utf-8"))
