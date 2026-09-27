"""Settings: where each value comes from, and what is refused.

Every test passes `_env_file` explicitly, so a developer's own .env never
leaks into the result, and clears the COFFEE_ variables it could see.
"""

import os
import tomllib

import pytest
from pydantic import ValidationError

from coffee.settings import PROJECT_ROOT, Settings

CONFIG_FILE = PROJECT_ROOT / "config" / "settings.toml"


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    for name in list(os.environ):
        if name.startswith("COFFEE_") or name == "OPENEXCHANGERATES_API_ID":
            monkeypatch.delenv(name)


def settings_from(toml_path=None, env_file=None) -> Settings:
    """Settings read from the given files: the committed TOML by default, and
    no .env unless one is passed."""

    class FromFiles(Settings):
        model_config = {
            **Settings.model_config,
            "toml_file": toml_path or CONFIG_FILE,
            "env_file": env_file,
        }

    return FromFiles()


# --------------------------------------------------------------------------
# Defaults
# --------------------------------------------------------------------------


def test_the_committed_config_file_matches_the_code_defaults():
    """config/settings.toml documents the defaults; the class carries them for
    when the file is absent. They must say the same thing."""
    committed = tomllib.loads(CONFIG_FILE.read_text())
    # Python values on both sides: TOML reads 2026-01-01 as a date.
    defaults = Settings.model_construct().model_dump()
    for key, value in committed.items():
        assert defaults[key] == value, key


def test_paths_default_to_the_project_root():
    settings = settings_from()
    assert settings.data_dir == PROJECT_ROOT / "data"
    assert settings.curated_dir == PROJECT_ROOT / "curated"
    assert settings.parsed_reviews == PROJECT_ROOT / "data" / "parsed" / "reviews.csv"
    assert (
        settings.roaster_decisions == PROJECT_ROOT / "curated" / "roaster_decisions.csv"
    )


# --------------------------------------------------------------------------
# Priority: environment > .env > TOML > defaults
# --------------------------------------------------------------------------


def test_the_toml_file_overrides_the_defaults(tmp_path):
    toml = tmp_path / "settings.toml"
    toml.write_text('log_level = "DEBUG"\n[scrape]\nconcurrency = 3\n')
    settings = settings_from(toml)
    assert (settings.log_level, settings.scrape.concurrency) == ("DEBUG", 3)
    assert settings.roasters.auto_threshold == 92  # untouched by the file


def test_dotenv_overrides_the_toml_file(tmp_path):
    toml = tmp_path / "settings.toml"
    toml.write_text("[scrape]\nconcurrency = 3\n")
    env = tmp_path / ".env"
    env.write_text("COFFEE_SCRAPE__CONCURRENCY=5\n")
    assert settings_from(toml, env).scrape.concurrency == 5


def test_the_environment_overrides_dotenv(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("COFFEE_SCRAPE__CONCURRENCY=5\n")
    monkeypatch.setenv("COFFEE_SCRAPE__CONCURRENCY", "7")
    assert settings_from(env_file=env).scrape.concurrency == 7


def test_dotenv_does_not_leak_into_the_process_environment(tmp_path):
    env = tmp_path / ".env"
    env.write_text("COFFEE_OPENEXCHANGERATES_API_ID=secret\n")
    settings_from(env_file=env)
    assert "COFFEE_OPENEXCHANGERATES_API_ID" not in os.environ


def test_an_empty_variable_means_the_default(monkeypatch):
    # `COFFEE_DATA_DIR=` copied from .env.example must not mean the cwd.
    monkeypatch.setenv("COFFEE_DATA_DIR", "")
    assert settings_from().data_dir == PROJECT_ROOT / "data"


def test_paths_can_be_moved(tmp_path, monkeypatch):
    monkeypatch.setenv("COFFEE_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.setenv("COFFEE_CURATED_DIR", str(tmp_path / "c"))
    settings = settings_from()
    assert settings.cleaned_reviews == tmp_path / "d" / "cleaned" / "reviews.parquet"
    assert settings.roaster_decisions == tmp_path / "c" / "roaster_decisions.csv"


# --------------------------------------------------------------------------
# The secret
# --------------------------------------------------------------------------


def test_the_api_key_is_optional_until_a_command_needs_it():
    assert settings_from().openexchangerates_api_id is None


def test_the_api_key_is_read_and_never_printed(monkeypatch):
    monkeypatch.setenv("COFFEE_OPENEXCHANGERATES_API_ID", "sk-real-key")
    settings = settings_from()
    assert settings.openexchangerates_api_id.get_secret_value() == "sk-real-key"
    assert "sk-real-key" not in repr(settings)
    assert "sk-real-key" not in str(settings)


def test_the_old_unprefixed_key_name_still_works(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OPENEXCHANGERATES_API_ID=old-name\n")
    key = settings_from(env_file=env).openexchangerates_api_id
    assert key.get_secret_value() == "old-name"


# --------------------------------------------------------------------------
# Validation at startup
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name, value",
    [
        ("COFFEE_SCRAPE__CONCURRENCY", "0"),
        ("COFFEE_SCRAPE__CONCURRENCY", "ten"),
        ("COFFEE_LOG_LEVEL", "LOUD"),
        ("COFFEE_PRICES__BASELINE_DATE", "January"),
        ("COFFEE_ROASTERS__AUTO_THRESHOLD", "101"),
    ],
)
def test_bad_values_are_refused(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    with pytest.raises(ValidationError):
        settings_from()


def test_a_review_band_above_the_auto_threshold_is_refused(monkeypatch):
    monkeypatch.setenv("COFFEE_ROASTERS__REVIEW_THRESHOLD", "95")
    with pytest.raises(ValidationError, match="must not exceed"):
        settings_from()
