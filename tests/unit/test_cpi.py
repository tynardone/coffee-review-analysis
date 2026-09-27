"""Tests for the CPI fetch.

The stored table is the only copy of a series going back to 1990, and the API
returns a three-year window. So the property that matters is that a narrow
response merges into a wide table rather than replacing it.
"""

import pandas as pd
import pytest

from coffee.cpi import (
    MONTH_COLUMNS,
    _observations_from_payload,
    fetch_cpi,
    load_cpi_table,
    merge_observations,
    save_cpi_table,
)


def payload(points, status="REQUEST_SUCCEEDED"):
    return {
        "status": status,
        "message": [],
        "Results": {"series": [{"seriesID": "CUUR0000SA0", "data": list(points)}]},
    }


def point(year, period, value):
    return {"year": str(year), "period": period, "value": str(value)}


def table(rows):
    """Build a wide table from {year: {month_name: value}}."""
    frame = pd.DataFrame(
        [
            {"Year": str(y), **{m: "" for m in MONTH_COLUMNS}, "HALF1": "", "HALF2": ""}
            for y in rows
        ]
    )
    for i, (_year, months) in enumerate(rows.items()):
        for month, value in months.items():
            frame.loc[i, month] = str(value)
    return frame


# --------------------------------------------------------------------------
# Reading the API response
# --------------------------------------------------------------------------


def test_monthly_observations_are_extracted():
    obs = _observations_from_payload(payload([point(2026, "M01", "325.252")]))
    assert obs == [(2026, 1, "325.252")]


def test_the_annual_average_is_not_treated_as_a_month():
    """M13 is the annual average; as a 13th month it would skew the series."""
    obs = _observations_from_payload(
        payload([point(2024, "M01", "308.417"), point(2024, "M13", "313.0")])
    )
    assert obs == [(2024, 1, "308.417")]


def test_an_unpublished_month_is_skipped():
    """BLS writes "-" for a month it never produced, as in October 2025."""
    obs = _observations_from_payload(
        payload([point(2025, "M10", "-"), point(2025, "M11", "324.122")])
    )
    assert obs == [(2025, 11, "324.122")]


def test_the_printed_value_is_kept_verbatim():
    """Round-tripping through float would turn 208.490 into 208.49."""
    obs = _observations_from_payload(payload([point(2007, "M09", "208.490")]))
    assert obs[0][2] == "208.490"


def test_a_failed_request_raises():
    with pytest.raises(RuntimeError, match="REQUEST_NOT_PROCESSED"):
        _observations_from_payload(payload([], status="REQUEST_NOT_PROCESSED"))


def test_an_empty_result_raises():
    with pytest.raises(RuntimeError, match="no series"):
        _observations_from_payload({"status": "REQUEST_SUCCEEDED", "Results": {}})


# --------------------------------------------------------------------------
# Merging into the stored table
# --------------------------------------------------------------------------


def test_a_new_month_fills_a_blank_cell():
    merged = merge_observations(table({1990: {"Jan": "127.4"}}), [(1990, 2, "128.0")])
    row = merged.iloc[0]
    assert row["Jan"] == "127.4" and row["Feb"] == "128.0"


def test_a_new_year_is_appended():
    merged = merge_observations(
        table({2024: {"Jan": "308.417"}}), [(2025, 1, "317.671")]
    )
    assert list(merged["Year"]) == ["2024", "2025"]


def test_months_outside_the_response_are_left_alone():
    """The API returns three years; the table holds thirty-five."""
    held = table({1990: {"Jan": "127.4"}, 2024: {"Jan": "308.417"}})
    merged = merge_observations(held, [(2024, 2, "310.326")])
    assert merged.loc[merged["Year"] == "1990", "Jan"].iloc[0] == "127.4"


def test_years_come_back_in_numeric_order():
    merged = merge_observations(table({2024: {}, 1990: {}}), [(2025, 1, "1.0")])
    assert list(merged["Year"]) == ["1990", "2024", "2025"]


def test_merge_does_not_mutate_its_input():
    held = table({2024: {"Jan": "308.417"}})
    merge_observations(held, [(2024, 2, "310.326")])
    assert held.loc[0, "Feb"] == ""


# --------------------------------------------------------------------------
# The fetch loop
# --------------------------------------------------------------------------


@pytest.fixture
def offline(monkeypatch):
    """Replace the network call with a settable response."""
    from coffee import cpi

    answers: list = []
    monkeypatch.setattr(cpi, "fetch_observations", lambda *a, **k: list(answers))
    return answers


def test_fetch_writes_the_merged_table(tmp_path, offline):
    path = tmp_path / "cpi.csv"
    save_cpi_table(table({2024: {"Jan": "308.417"}}), path)
    offline.append((2024, 2, "310.326"))

    fetch_cpi(path)

    held = load_cpi_table(path)
    assert held.loc[0, "Jan"] == "308.417"
    assert held.loc[0, "Feb"] == "310.326"


def test_fetch_refuses_to_shrink_the_table(tmp_path, offline, monkeypatch):
    """A response that would drop months must not overwrite the only copy."""
    from coffee import cpi

    path = tmp_path / "cpi.csv"
    save_cpi_table(table({2024: {"Jan": "308.417", "Feb": "310.326"}}), path)
    monkeypatch.setattr(cpi, "merge_observations", lambda held, obs: table({2024: {}}))

    with pytest.raises(ValueError, match="Refusing to write"):
        fetch_cpi(path)

    assert load_cpi_table(path).loc[0, "Jan"] == "308.417"


def test_fetch_against_a_missing_file_creates_it(tmp_path, offline):
    path = tmp_path / "nested" / "cpi.csv"
    offline.append((2026, 1, "325.252"))

    fetch_cpi(path)

    assert load_cpi_table(path).loc[0, "Jan"] == "325.252"
