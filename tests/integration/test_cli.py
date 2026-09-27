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
from coffee.page_store import PageStore
from coffee.settings import Settings
from tests.paths import GOLDEN, review_pages


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    """Empty data and seed directories, and no developer .env."""
    data, seeds = tmp_path / "data", tmp_path / "seeds"
    monkeypatch.setenv("COFFEE_DATA_DIR", str(data))
    monkeypatch.setenv("COFFEE_SEEDS_DIR", str(seeds))
    # A developer's .env could supply the API key or redirect the data dir,
    # and the real get_settings() caches its first result across tests.
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None))
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
    monkeypatch.delenv("COFFEE_OPENEXCHANGERATES_API_ID", raising=False)
    monkeypatch.delenv("OPENEXCHANGERATES_API_ID", raising=False)
    with pytest.raises(SystemExit, match="COFFEE_OPENEXCHANGERATES_API_ID is not set"):
        cli.fetch_exchange_rates([])


def test_parse_reviews_turns_saved_pages_into_the_golden_rows(dirs):
    """The real pages, saved to bronze and parsed by the command, give exactly
    the fields the parser's golden file pins, plus where each came from."""
    data, _ = dirs
    pages = PageStore(data / "bronze" / "reviews")
    for path in review_pages():
        url = f"https://www.coffeereview.com/review/{path.stem}/"
        pages.save(url, path.read_text(encoding="utf-8"), None, "2026-09-27T12:00Z")

    cli.parse_reviews([])

    held = pd.read_csv(data / "raw" / "reviews.csv", dtype=str, keep_default_na=False)
    held = held.set_index("url")
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert len(held) == len(golden) == 10
    for name, fields in golden.items():
        row = held.loc[f"https://www.coffeereview.com/review/{name[:-5]}/"]
        assert {k: row[k] for k in fields} == {
            k: "" if v is None else v for k, v in fields.items()
        }
        assert row["scraped_at"] == "2026-09-27T12:00Z"


def test_parse_reviews_with_nothing_saved_writes_nothing(dirs, capsys):
    data, _ = dirs
    cli.parse_reviews([])
    assert "parsed 0 page(s)" in capsys.readouterr().out
    assert not (data / "raw" / "reviews.csv").exists()
