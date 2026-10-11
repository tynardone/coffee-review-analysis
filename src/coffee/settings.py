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

A name that matches no setting is an error, not ignored, so a typo such as
``COFFEE_SCRAPE__CONCURENCY`` cannot leave the default quietly in place. Lines in
``.env`` without the ``COFFEE_`` prefix are left alone; they may belong to
another tool.

Library code never reads the environment. Entry points (the CLI, notebooks)
call :func:`get_settings` and pass the values they need down.
"""

import os
import tomllib
from collections.abc import Mapping
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal, Self

from dotenv import dotenv_values
from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
    TomlConfigSettingsSource,
)

__all__ = [
    "PROJECT_ROOT",
    "Settings",
    "UnknownSettingError",
    "find_unknown_names",
    "get_settings",
]


def _find_project_root(
    start: Path | None = None, marker: str = "pyproject.toml"
) -> Path:
    """Walk up from this file until a directory containing ``marker`` is found.

    Resolving from this file rather than the working directory keeps the
    defaults stable for a script, a test, or a notebook started anywhere.

    Stops with an error rather than guessing. The settings file and the data
    live in the repository, so a copy of the package installed elsewhere, as
    a non-editable install puts it, has nothing sensible to fall back on.
    """
    start = (start or Path(__file__)).resolve()
    for parent in start.parents:
        if (parent / marker).exists():
            return parent
    raise RuntimeError(
        f"Could not find {marker} in any folder above {start}. The coffee "
        "package reads its settings and data from the repository, so install "
        "it from a checkout in editable mode: run `uv sync` there."
    )


PROJECT_ROOT: Path = _find_project_root()


class UnknownSettingError(ValueError):
    """A setting name matched nothing, so its value would be ignored."""


class _Section(BaseModel):
    # A misspelled key inside a table, such as `[scrape] concurency`, is an
    # error rather than a value silently dropped.
    model_config = ConfigDict(extra="forbid")


class ScrapeSettings(_Section):
    concurrency: int = Field(default=10, ge=1)
    lookback_hours: int = Field(default=24, ge=0)


class PriceSettings(_Section):
    baseline_date: date = date(2026, 1, 1)

    @field_validator("baseline_date")
    @classmethod
    def _first_of_a_month(cls, value: date) -> date:
        # CPI is published per month, keyed on its first day, so any other
        # day would fail later with a misleading "no CPI for this date".
        if value.day != 1:
            raise ValueError(
                f"must be the first day of a month, such as "
                f"{value.replace(day=1).isoformat()}: CPI is monthly"
            )
        return value


class RoasterSettings(_Section):
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

    @field_validator("data_dir", "curated_dir")
    @classmethod
    def _relative_to_project_root(cls, value: Path) -> Path:
        # Resolved against the repository, not the working directory, so a
        # command run from another folder cannot create a fresh, empty data
        # directory there.
        value = value.expanduser()
        return value if value.is_absolute() else PROJECT_ROOT / value

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


def _known_env_names(settings_cls: type[BaseSettings]) -> set[str]:
    """Every variable name that sets something, upper-cased."""
    config = settings_cls.model_config
    prefix = config.get("env_prefix", "")
    delimiter = config.get("env_nested_delimiter") or "__"
    known: set[str] = set()
    for name, field in settings_cls.model_fields.items():
        known.add(f"{prefix}{name}".upper())
        if isinstance(field.validation_alias, AliasChoices):
            known.update(
                str(choice).upper() for choice in field.validation_alias.choices
            )
        section = field.annotation
        if isinstance(section, type) and issubclass(section, BaseModel):
            known.update(
                f"{prefix}{name}{delimiter}{key}".upper()
                for key in section.model_fields
            )
    return known


def find_unknown_names(
    settings_cls: type[BaseSettings],
    environ: Mapping[str, str],
    env_file: Path | None,
    toml_file: Path | None,
) -> list[str]:
    """Names in the environment, ``.env`` or the TOML file that set nothing.

    Only ``COFFEE_`` names are checked in the environment and ``.env``, since
    other variables belong to other tools. In the TOML file, every top-level
    key must be a setting; a misspelled key inside a table is caught by the
    table's own model.
    """
    prefix = settings_cls.model_config.get("env_prefix", "").upper()
    known = _known_env_names(settings_cls)

    def unknown_in(names: list[str], where: str) -> list[str]:
        return [
            f"{name} in {where}"
            for name in sorted(names)
            if name.upper().startswith(prefix) and name.upper() not in known
        ]

    found = unknown_in(list(environ), "the environment")
    if env_file is not None and env_file.exists():
        found += unknown_in(list(dotenv_values(env_file)), env_file.name)
    if toml_file is not None and toml_file.exists():
        sections = tomllib.loads(toml_file.read_text(encoding="utf-8"))
        found += [
            f"{key} in {toml_file.name}"
            for key in sorted(sections)
            if key not in settings_cls.model_fields
        ]
    return found


@lru_cache
def get_settings() -> Settings:
    """The settings, loaded and validated on first call and reused after.

    Raises :class:`UnknownSettingError` for a name that sets nothing, after
    pydantic has checked the values themselves.
    """
    settings = Settings()
    config = Settings.model_config
    env_file, toml_file = config.get("env_file"), config.get("toml_file")
    unknown = find_unknown_names(
        Settings,
        os.environ,
        Path(env_file) if isinstance(env_file, (str, Path)) else None,
        Path(toml_file) if isinstance(toml_file, (str, Path)) else None,
    )
    if unknown:
        raise UnknownSettingError(
            "These names match no setting, so their values would be ignored. "
            "Check the spelling against config/settings.toml:\n"
            + "\n".join(f"  {name}" for name in unknown)
        )
    return settings
