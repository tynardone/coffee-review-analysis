"""The pipeline's settings: one typed object, loaded and validated once.

Values fall into three kinds, kept apart:

- **Config** that you might change without editing Python: where data lives,
  the log level, scrape concurrency, the inflation baseline, the roaster
  thresholds. Defaults are committed in ``config/settings.toml``.
- **Secrets**: the OpenExchangeRates app ID. Only ever in the environment or
  ``.env``, never in a committed file.
- **Constants** that are part of the code's logic: site URLs, timeouts, retry
  rules, parsing tables. These live next to the code that uses them.

Sources, highest priority first:

1. arguments passed to ``Settings(...)`` (tests)
2. environment variables, ``COFFEE_*``
3. ``.env`` at the project root
4. ``config/settings.toml``
5. the defaults below

A value inside a table is set with a double underscore, so
``COFFEE_SCRAPE__CONCURRENCY=4`` overrides ``[scrape] concurrency``.
Command-line flags override all of these for a single run.

Library code never reads the environment. Entry points (the CLI, notebooks)
call :func:`get_settings` and pass the values they need down.
"""

from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from pydantic import AliasChoices, BaseModel, Field, SecretStr, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

__all__ = ["PROJECT_ROOT", "Settings", "get_settings"]


def _find_project_root(marker: str = "pyproject.toml") -> Path:
    """Walk up from this file until a directory containing ``marker`` is found.

    Resolving from this file rather than the working directory keeps the
    defaults stable for a script, a test, or a notebook started anywhere.
    """
    start = Path(__file__).resolve()
    for parent in start.parents:
        if (parent / marker).exists():
            return parent
    return start.parent.parent


PROJECT_ROOT: Path = _find_project_root()


class ScrapeSettings(BaseModel):
    concurrency: int = Field(default=10, ge=1)
    lookback_hours: int = Field(default=24, ge=0)


class PriceSettings(BaseModel):
    baseline_date: date = date(2026, 1, 1)


class RoasterSettings(BaseModel):
    auto_threshold: int = Field(default=92, ge=0, le=100)
    review_threshold: int = Field(default=82, ge=0, le=100)

    @model_validator(mode="after")
    def _review_band_below_auto(self) -> Self:
        if self.review_threshold > self.auto_threshold:
            raise ValueError(
                f"review_threshold ({self.review_threshold}) must not exceed "
                f"auto_threshold ({self.auto_threshold})"
            )
        return self


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COFFEE_",
        env_nested_delimiter="__",
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        # `COFFEE_DATA_DIR=` copied from .env.example means "use the default",
        # not "use the current directory".
        env_ignore_empty=True,
        toml_file=PROJECT_ROOT / "config" / "settings.toml",
        extra="ignore",
        validate_by_name=True,
    )

    data_dir: Path = PROJECT_ROOT / "data"
    curated_dir: Path = PROJECT_ROOT / "curated"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    scrape: ScrapeSettings = ScrapeSettings()
    prices: PriceSettings = PriceSettings()
    roasters: RoasterSettings = RoasterSettings()

    # Optional here because only `fetch-exchange-rates` needs it; that command
    # stops before doing any work when it is missing. The unprefixed name is
    # still accepted so an existing .env keeps working.
    openexchangerates_api_id: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices(
            "COFFEE_OPENEXCHANGERATES_API_ID", "OPENEXCHANGERATES_API_ID"
        ),
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # The documented order: arguments, environment, .env, the TOML file,
        # then the field defaults.
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            TomlConfigSettingsSource(settings_cls),
        )

    # -- data/, in pipeline order -----------------------------------------

    @property
    def downloaded_dir(self) -> Path:
        """Review pages as the site served them, plus their manifest."""
        return self.data_dir / "downloaded"

    @property
    def parsed_reviews(self) -> Path:
        """One row per review, parsed from its downloaded page."""
        return self.data_dir / "parsed" / "reviews.csv"

    @property
    def reference_dir(self) -> Path:
        return self.data_dir / "reference"

    @property
    def exchange_rates(self) -> Path:
        return self.reference_dir / "exchange_rates.json"

    @property
    def cpi(self) -> Path:
        return self.reference_dir / "cpi.csv"

    @property
    def roasters_dir(self) -> Path:
        return self.data_dir / "roasters"

    @property
    def roaster_crosswalk(self) -> Path:
        return self.roasters_dir / "crosswalk.csv"

    @property
    def roaster_review_queue(self) -> Path:
        return self.roasters_dir / "review_queue.csv"

    @property
    def cleaned_reviews(self) -> Path:
        """Typed and priced, with roasters resolved: what the notebooks read."""
        return self.data_dir / "cleaned" / "reviews.parquet"

    # -- curated/: kept by hand, nothing regenerates it --------------------

    @property
    def roaster_decisions(self) -> Path:
        return self.curated_dir / "roaster_decisions.csv"


@lru_cache
def get_settings() -> Settings:
    """The settings, loaded and validated on first call and reused after."""
    return Settings()
