"""Where things live on this machine, and how secrets are read.

This module holds only what differs between machines: the data directory and
the seed directory. Behaviour (site URLs, timeouts, thresholds) lives in the
module that uses it, and secrets stay in the environment.

Library code never reads ``os.environ`` itself. A :class:`Settings` is built
once by whoever is driving the work (a CLI command, a notebook, a test) and its
paths are passed down, so every step can be pointed at a temporary directory.

``PROJECT_ROOT`` is resolved from this file rather than the current working
directory, so the defaults are stable whether the package is used from a
script, a test, or a notebook started in any directory.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

__all__ = ["PROJECT_ROOT", "Settings", "load_env", "require_env"]


def _find_project_root(marker: str = "pyproject.toml") -> Path:
    """Walk up from this file until a directory containing ``marker`` is found.

    Falls back to the directory above the package when no marker is found.
    """
    start = Path(__file__).resolve()
    for parent in start.parents:
        if (parent / marker).exists():
            return parent
    return start.parent.parent


PROJECT_ROOT: Path = _find_project_root()


def load_env() -> None:
    """Read ``.env`` at the project root into the environment, if present.

    A variable already set in the real environment wins over the file, so a
    scheduler or CI can override anything in it. Called by entry points, never
    at import, so importing the package has no side effects.
    """
    load_dotenv(PROJECT_ROOT / ".env")


def require_env(name: str) -> str:
    """The value of environment variable ``name``, or an error naming it.

    The message names the variable, never its value.
    """
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"environment variable {name} is not set (see .env.example)")
    return value


@dataclass(frozen=True)
class Settings:
    """The two roots every file hangs off.

    ``data_dir`` holds everything the pipeline writes or fetches, all of which
    can be rebuilt. ``seeds_dir`` holds hand-kept reference data that nothing
    can regenerate, such as the adjudicated roaster decisions.
    """

    data_dir: Path
    seeds_dir: Path

    @classmethod
    def from_env(cls, environ: Mapping[str, str] = os.environ) -> "Settings":
        """Build from ``COFFEE_DATA_DIR`` and ``COFFEE_SEEDS_DIR``.

        Either may be unset, in which case it defaults to ``data/`` or
        ``seeds/`` at the project root.
        """
        return cls(
            data_dir=Path(environ.get("COFFEE_DATA_DIR") or PROJECT_ROOT / "data"),
            seeds_dir=Path(environ.get("COFFEE_SEEDS_DIR") or PROJECT_ROOT / "seeds"),
        )

    # -- data/ -------------------------------------------------------------

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def raw_reviews(self) -> Path:
        return self.raw_dir / "reviews.csv"

    @property
    def clean_reviews(self) -> Path:
        return self.data_dir / "clean" / "reviews.parquet"

    @property
    def roasters_dir(self) -> Path:
        return self.data_dir / "roasters"

    @property
    def roaster_crosswalk(self) -> Path:
        return self.roasters_dir / "roaster_crosswalk.csv"

    @property
    def exchange_rates(self) -> Path:
        return self.data_dir / "external" / "openex_exchange_rates.json"

    @property
    def cpi(self) -> Path:
        return self.data_dir / "external" / "consumer_price_index.csv"

    # -- seeds/ ------------------------------------------------------------

    @property
    def roaster_decisions(self) -> Path:
        return self.seeds_dir / "roaster_decisions.csv"
