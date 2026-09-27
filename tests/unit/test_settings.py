"""Settings: default paths, environment overrides, and reading secrets."""

import os

import pytest

from coffee import settings as settings_module
from coffee.settings import PROJECT_ROOT, Settings, load_env, require_env


def test_defaults_hang_off_the_project_root():
    settings = Settings.from_env({})
    assert settings.data_dir == PROJECT_ROOT / "data"
    assert settings.seeds_dir == PROJECT_ROOT / "seeds"
    assert settings.raw_reviews == PROJECT_ROOT / "data" / "raw" / "reviews.csv"


def test_environment_overrides_both_roots(tmp_path):
    settings = Settings.from_env(
        {
            "COFFEE_DATA_DIR": str(tmp_path / "d"),
            "COFFEE_SEEDS_DIR": str(tmp_path / "s"),
        }
    )
    assert settings.clean_reviews == tmp_path / "d" / "clean" / "reviews.parquet"
    assert settings.roaster_decisions == tmp_path / "s" / "roaster_decisions.csv"


def test_an_empty_variable_means_the_default():
    # `COFFEE_DATA_DIR=` copied from .env.example must not point at the cwd.
    assert Settings.from_env({"COFFEE_DATA_DIR": ""}).data_dir == PROJECT_ROOT / "data"


def test_decisions_live_with_the_seeds_not_the_derived_roaster_files(tmp_path):
    settings = Settings(data_dir=tmp_path / "data", seeds_dir=tmp_path / "seeds")
    assert settings.roaster_crosswalk.parent == settings.roasters_dir
    assert settings.roaster_decisions.parent == settings.seeds_dir


def test_require_env_names_the_missing_variable(monkeypatch):
    monkeypatch.delenv("COFFEE_TEST_SECRET", raising=False)
    with pytest.raises(RuntimeError, match="COFFEE_TEST_SECRET is not set"):
        require_env("COFFEE_TEST_SECRET")


def test_require_env_treats_empty_as_missing(monkeypatch):
    monkeypatch.setenv("COFFEE_TEST_SECRET", "")
    with pytest.raises(RuntimeError):
        require_env("COFFEE_TEST_SECRET")


def test_real_environment_wins_over_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("COFFEE_TEST_A=from-file\nCOFFEE_TEST_B=from-file\n")
    monkeypatch.setattr(settings_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("COFFEE_TEST_A", "from-env")
    monkeypatch.delenv("COFFEE_TEST_B", raising=False)

    load_env()

    assert os.environ["COFFEE_TEST_A"] == "from-env"
    assert os.environ["COFFEE_TEST_B"] == "from-file"
