"""Shared test fixtures."""

import json

import pytest

from tests.paths import GOLDEN


@pytest.fixture(scope="session")
def golden() -> dict:
    """The expected parse of every fixture page, keyed by filename."""
    return json.loads(GOLDEN.read_text(encoding="utf-8"))
