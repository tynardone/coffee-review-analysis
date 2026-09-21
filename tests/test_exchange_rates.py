"""Tests for the incremental exchange-rate fetch.

The stored rate file is the only copy of data bought against a 1000 request
per month quota, so the properties worth pinning are about not destroying it:
a date already held is not re-requested, and a failed request never replaces a
populated entry with a blank one.
"""

import json
from datetime import date

import pandas as pd
import pytest

from coffee.exchange_rates import (
    fetch_rates,
    load_rates,
    load_review_dates,
    merge_rates,
    save_rates,
    unfetched_dates,
)

JAN = date(2000, 1, 1)
FEB = date(2000, 2, 1)
MAR = date(2000, 3, 1)

USD_ONLY = {"USD": 1.0}
WITH_EUR = {"USD": 1.0, "EUR": 0.9}


# --------------------------------------------------------------------------
# Planning which dates to request
# --------------------------------------------------------------------------


def test_a_date_never_fetched_is_pending():
    assert unfetched_dates([JAN], {}) == [JAN]


def test_a_date_already_held_is_skipped():
    assert unfetched_dates([JAN], {"2000-01-01": USD_ONLY}) == []


def test_a_date_held_as_empty_is_retried():
    """An empty entry records a failed request, not a result.

    Treating it as held would let a single rate-limited run leave that date
    permanently blank, since nothing would ever ask for it again.
    """
    assert unfetched_dates([JAN], {"2000-01-01": {}}) == [JAN]


def test_only_the_missing_dates_are_pending():
    held = {"2000-01-01": USD_ONLY, "2000-03-01": USD_ONLY}
    assert unfetched_dates([JAN, FEB, MAR], held) == [FEB]


def test_pending_dates_keep_their_order():
    assert unfetched_dates([MAR, JAN, FEB], {}) == [MAR, JAN, FEB]


# --------------------------------------------------------------------------
# Merging: the property that protects the file
# --------------------------------------------------------------------------


def test_an_empty_result_never_displaces_a_populated_one():
    """The regression test for the bug this change exists to fix."""
    held = {"2000-01-01": WITH_EUR}
    assert merge_rates(held, {"2000-01-01": {}}) == held


def test_a_populated_result_replaces_a_held_one():
    merged = merge_rates({"2000-01-01": USD_ONLY}, {"2000-01-01": WITH_EUR})
    assert merged == {"2000-01-01": WITH_EUR}


def test_a_new_date_is_added():
    merged = merge_rates({"2000-01-01": USD_ONLY}, {"2000-02-01": WITH_EUR})
    assert merged == {"2000-01-01": USD_ONLY, "2000-02-01": WITH_EUR}


def test_an_empty_result_is_recorded_when_nothing_is_held():
    assert merge_rates({}, {"2000-01-01": {}}) == {"2000-01-01": {}}


def test_merge_does_not_mutate_its_inputs():
    held = {"2000-01-01": USD_ONLY}
    merge_rates(held, {"2000-01-01": WITH_EUR})
    assert held == {"2000-01-01": USD_ONLY}


# --------------------------------------------------------------------------
# Round trip
# --------------------------------------------------------------------------


def test_load_rates_of_a_missing_file_is_empty(tmp_path):
    assert load_rates(tmp_path / "absent.json") == {}


def test_save_then_load_round_trips(tmp_path):
    path = tmp_path / "nested" / "rates.json"
    rates = {"2000-02-01": WITH_EUR, "2000-01-01": USD_ONLY}
    save_rates(rates, path)
    assert load_rates(path) == rates


def test_saved_keys_are_sorted(tmp_path):
    path = tmp_path / "rates.json"
    save_rates({"2000-03-01": USD_ONLY, "2000-01-01": USD_ONLY}, path)
    assert list(json.loads(path.read_text())) == ["2000-01-01", "2000-03-01"]


# --------------------------------------------------------------------------
# The fetch loop
# --------------------------------------------------------------------------


@pytest.fixture
def recording_fetch(monkeypatch):
    """Replace the network call, recording which dates were requested."""
    from coffee import exchange_rates

    requested: list[date] = []
    answers: dict[date, dict[str, float] | None] = {}

    def fake_fetch_rate(session, day, app_id):
        requested.append(day)
        return answers.get(day, USD_ONLY)

    monkeypatch.setattr(exchange_rates, "fetch_rate", fake_fetch_rate)
    monkeypatch.setattr(exchange_rates, "_build_session", lambda *a, **k: None)
    return requested, answers


def test_held_dates_are_not_requested(tmp_path, recording_fetch):
    requested, _ = recording_fetch
    path = tmp_path / "rates.json"
    save_rates({"2000-01-01": USD_ONLY}, path)

    fetch_rates([JAN, FEB], "key", path)

    assert requested == [FEB]


def test_a_second_run_requests_nothing(tmp_path, recording_fetch):
    """Idempotence is the whole point: re-running must not spend quota."""
    requested, _ = recording_fetch
    path = tmp_path / "rates.json"

    fetch_rates([JAN, FEB], "key", path)
    requested.clear()
    fetch_rates([JAN, FEB], "key", path)

    assert requested == []


def test_refetch_requests_everything(tmp_path, recording_fetch):
    requested, _ = recording_fetch
    path = tmp_path / "rates.json"
    save_rates({"2000-01-01": USD_ONLY}, path)

    fetch_rates([JAN, FEB], "key", path, refetch=True)

    assert requested == [JAN, FEB]


def test_a_failed_fetch_leaves_the_held_value_intact(tmp_path, recording_fetch):
    _, answers = recording_fetch
    path = tmp_path / "rates.json"
    save_rates({"2000-01-01": WITH_EUR}, path)
    answers[JAN] = None  # the request fails

    result = fetch_rates([JAN], "key", path, refetch=True)

    assert result["2000-01-01"] == WITH_EUR
    assert load_rates(path)["2000-01-01"] == WITH_EUR


def test_a_failed_date_is_retried_on_the_next_run(tmp_path, recording_fetch):
    requested, answers = recording_fetch
    path = tmp_path / "rates.json"
    answers[JAN] = None

    fetch_rates([JAN], "key", path)
    requested.clear()
    answers.pop(JAN)  # the retry succeeds
    fetch_rates([JAN], "key", path)

    assert requested == [JAN]
    assert load_rates(path)["2000-01-01"] == USD_ONLY


def test_progress_is_checkpointed_before_the_run_ends(tmp_path, monkeypatch):
    """A run that dies partway must keep the requests it already spent."""
    from coffee import exchange_rates

    path = tmp_path / "rates.json"
    days = [date(2000, month, 1) for month in range(1, 13)]
    boom = days[7]

    def exploding_fetch(session, day, app_id):
        if day == boom:
            raise KeyboardInterrupt
        return USD_ONLY

    monkeypatch.setattr(exchange_rates, "fetch_rate", exploding_fetch)
    monkeypatch.setattr(exchange_rates, "_build_session", lambda *a, **k: None)

    with pytest.raises(KeyboardInterrupt):
        fetch_rates(days, "key", path, checkpoint_every=3)

    # Checkpoints land after the 3rd and 6th fetch; the 8th raises.
    assert len(load_rates(path)) == 6


def test_nothing_is_written_when_nothing_is_pending(tmp_path, recording_fetch):
    requested, _ = recording_fetch
    path = tmp_path / "rates.json"
    save_rates({"2000-01-01": USD_ONLY}, path)
    before = path.read_text()

    fetch_rates([JAN], "key", path)

    assert requested == []
    assert path.read_text() == before


# --------------------------------------------------------------------------
# Reading review months out of a data layer
# --------------------------------------------------------------------------


def _write_reviews(tmp_path, values, name="reviews.csv"):
    path = tmp_path / name
    pd.DataFrame({"review_date": values}).to_csv(path, index=False)
    return path


def test_review_dates_are_read_from_the_cleaned_layer(tmp_path):
    """The cleaned layer stores ISO dates."""
    path = _write_reviews(tmp_path, ["2000-01-01", "2000-02-01", "2000-01-01"])
    assert load_review_dates(path) == [JAN, FEB]


def test_review_dates_are_also_read_from_the_raw_layer(tmp_path):
    """The raw layer stores the site's own "Month Year", used when
    bootstrapping before a cleaned layer exists."""
    path = _write_reviews(tmp_path, ["January 2000", "February 2000"])
    assert load_review_dates(path) == [JAN, FEB]


def test_review_dates_are_deduplicated_and_sorted(tmp_path):
    path = _write_reviews(tmp_path, ["2000-03-01", "2000-01-01", "2000-03-01"])
    assert load_review_dates(path) == [JAN, MAR]


def test_dates_before_the_api_history_are_excluded(tmp_path):
    """OpenExchangeRates has no data before 1999, so requesting it wastes quota."""
    path = _write_reviews(tmp_path, ["1996-05-01", "2000-01-01"])
    assert load_review_dates(path) == [JAN]


def test_a_missing_reviews_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_review_dates(tmp_path / "absent.csv")


def test_an_unsupported_suffix_raises(tmp_path):
    (tmp_path / "reviews.xlsx").touch()
    with pytest.raises(ValueError, match="Unsupported file type"):
        load_review_dates(tmp_path / "reviews.xlsx")


def test_review_dates_are_read_from_the_parquet_cleaned_layer(tmp_path):
    """The cleaned layer is Parquet, and keeps review_date as a datetime."""
    path = tmp_path / "reviews.parquet"
    pd.DataFrame(
        {"review_date": pd.to_datetime(["2000-01-01", "2000-02-01"])}
    ).to_parquet(path)
    assert load_review_dates(path) == [JAN, FEB]
