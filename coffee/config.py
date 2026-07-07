"""Project configuration and paths.

Exposes a robust ``PROJECT_ROOT`` that resolves the repository root regardless
of the current working directory, so it can be imported from scripts and
notebooks alike (``from coffee.config import PROJECT_ROOT``).
"""

import os
from pathlib import Path

from dotenv import load_dotenv


def _find_project_root(marker: str = "pyproject.toml") -> Path:
    """Walk up from this file until a directory containing ``marker`` is found.

    Resolving from ``__file__`` (not the current working directory) makes this
    stable whether it's imported from a script, a test, or a notebook running
    from any directory. Falls back to the package's parent directory if no
    marker is found.
    """
    start = Path(__file__).resolve()
    for parent in start.parents:
        if (parent / marker).exists():
            return parent
    return start.parent.parent


PROJECT_ROOT: Path = _find_project_root()

# Load environment variables from a .env file at the project root, if present.
load_dotenv(PROJECT_ROOT / ".env")


class Config:
    PROJECT_ROOT: Path = PROJECT_ROOT
    DATA_DIR: Path = PROJECT_ROOT / "data"
    # Kept as an alias for backwards compatibility; prefer PROJECT_ROOT.
    BASEDIR: Path = PROJECT_ROOT
    OPENEXCHANGERATES_API_ID = os.environ.get("OPENEXCHANGERATES_API_ID")
    GEOCODE_API_KEY = os.environ.get("GEOCODE_API_KEY")
    BASE_URL = "https://www.coffeereview.com/review/"
    HEADERS = {
        "user-agent": (
            "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
        )
    }


class OpenExConfig(Config):
    API_URL: str = "https://openexchangerates.org/api/historical/"
    TIMEOUT: int = 10
