"""Project paths, site URLs, and API credentials.

``PROJECT_ROOT`` is resolved from this file rather than the current working
directory, so it is stable whether imported from a script, a test, or a
notebook started anywhere. Everything else hangs off it.

Credentials are read through a function, not bound at import: a module-level
``os.environ.get`` is evaluated once when the module first loads, so exporting
the variable afterwards — routine in a notebook — would silently have no
effect.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

__all__ = [
    "BASE_URL",
    "DATA_DIR",
    "HEADERS",
    "OPENEX_API_URL",
    "OPENEX_TIMEOUT",
    "PROJECT_ROOT",
    "SITEMAP_URL",
    "openexchangerates_api_id",
]


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

DATA_DIR: Path = PROJECT_ROOT / "data"

# Review discovery reads the sitemap index; BASE_URL is the human-facing
# listing page, kept for reference and for constructing review URLs by hand.
BASE_URL = "https://www.coffeereview.com/review/"
SITEMAP_URL = "https://www.coffeereview.com/sitemap_index.xml"

HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36"
    )
}

OPENEX_API_URL = "https://openexchangerates.org/api/historical/"
OPENEX_TIMEOUT = 10


def openexchangerates_api_id() -> str | None:
    """The OpenExchangeRates app id, or None if it is not configured.

    Read on each call so that setting the variable after import still works.
    """
    return os.environ.get("OPENEXCHANGERATES_API_ID")
