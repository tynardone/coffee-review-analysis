"""The console commands against a fresh, empty data directory.

These run the real entry points with ``COFFEE_DATA_DIR`` and
``COFFEE_SEEDS_DIR`` pointed at a temporary directory, so they prove that every
default path comes from the settings rather than from the repository's own
``data/``. No network: only the commands that work from files are run.
"""

import json

import pandas as pd
import pytest

from coffee import cli
from tests.paths import GOLDEN


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Empty data and seed directories, and no developer .env."""
    data, seeds = tmp_path / "data", tmp_path / "seeds"
    monkeypatch.setenv("COFFEE_DATA_DIR", str(data))
    monkeypatch.setenv("COFFEE_SEEDS_DIR", str(seeds))
    # A developer's .env could supply the API key or redirect the data dir.
    monkeypatch.setattr(cli, "load_env", lambda: None)
    return data, seeds


@pytest.fixture
def raw_reviews(dirs):
    """The ten fixture pages' parse, written where the scrape would put it."""
    data, _ = dirs
    records = [
        {**fields, "url": f"https://www.coffeereview.com/review/{name[:-5]}/"}
        for name, fields in json.loads(GOLDEN.read_text(encoding="utf-8")).items()
    ]
    path = data / "raw" / "reviews.csv"
    path.parent.mkdir(parents=True)
    # Every fixture page post-dates the site's 2017-18 rename to
    # `acidity/structure`, so none carries the bare `acidity` the full corpus
    # always has and cleaning expects.
    pd.DataFrame(records).assign(acidity=None).to_csv(path, index=False)
    return path


def test_clean_reviews_writes_under_the_configured_data_dir(dirs, raw_reviews):
    data, _ = dirs
    cli.clean_reviews_command([])

    cleaned = pd.read_parquet(data / "clean" / "reviews.parquet")
    assert len(cleaned) == 10
    assert cleaned["url"].is_unique


def test_resolve_roasters_reads_decisions_from_the_seeds_dir(dirs, raw_reviews, capsys):
    data, seeds = dirs
    seeds.mkdir()
    (seeds / "roaster_decisions.csv").write_text(
        "name_a,name_b,verdict,decided_by,decided_on,note\n"
        "1980 CAFE,U&Me Buna,split,test,2026-09-27,\n"
    )

    cli.resolve_roasters([str(raw_reviews)])

    assert (data / "roasters" / "roaster_crosswalk.csv").exists()
    assert not (data / "roasters" / "roaster_decisions.csv").exists()
    assert f"1 decisions applied from {seeds / 'roaster_decisions.csv'}" in (
        capsys.readouterr().out
    )


def test_fetch_exchange_rates_without_a_key_exits_naming_the_variable(
    dirs, raw_reviews, monkeypatch
):
    monkeypatch.delenv("OPENEXCHANGERATES_API_ID", raising=False)
    with pytest.raises(SystemExit, match="OPENEXCHANGERATES_API_ID is not set"):
        cli.fetch_exchange_rates([])
