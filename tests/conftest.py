"""Shared test fixtures."""

import json

import pytest

from tests.paths import GOLDEN


@pytest.fixture(scope="session")
def golden() -> dict[str, dict[str, str | None]]:
    """The expected parse of every fixture page, keyed by filename."""
    expected: dict[str, dict[str, str | None]] = json.loads(
        GOLDEN.read_text(encoding="utf-8")
    )
    return expected
