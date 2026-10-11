"""Tests for the price conversions.

These conversions decide what every price comparison means, and a bug in them
is invisible: a wrong rate or a missing CPI month raises no error but changes
any conclusion drawn from the prices.
"""

from datetime import date

import pandas as pd
import pytest

from coffee.prices import (
    PRICE_STATUSES,
    add_comparable_prices,
    add_price_status,
    attach_cpi,
    convert_currency,
    price_per_lb,
    to_baseline_dollars,
)


def rates(rows):
    return pd.DataFrame(rows, columns=["review_date", "price_currency", "rate"]).assign(
        review_date=lambda d: pd.to_datetime(d["review_date"])
    )


def cpi_table(rows):
    return pd.DataFrame(rows, columns=["cpi", "date"]).assign(
        date=lambda d: pd.to_datetime(d["date"])
    )


def priced(price_usd, cpi, per_lb=None):
    """One cleaned review's price columns, as to_baseline_dollars reads them."""
    return pd.DataFrame(
        {"price_usd": [price_usd], "price_usd_per_lb": [per_lb], "cpi": [cpi]}
    )


# --------------------------------------------------------------------------
# Currency
# --------------------------------------------------------------------------


def test_prices_convert_at_the_review_months_rate():
    df = pd.DataFrame(
        {
            "review_date": pd.to_datetime(["2024-07-01"]),
            "price_currency": ["TWD"],
            "price_value": [500.0],
        }
    )
    out = convert_currency(df, rates([("2024-07-01", "TWD", 32.0)]))
    assert out["price_usd"].iloc[0] == pytest.approx(15.62)


def test_a_missing_rate_leaves_the_usd_price_missing():
    df = pd.DataFrame(
        {
            "review_date": pd.to_datetime(["1998-01-01"]),
            "price_currency": ["TWD"],
            "price_value": [500.0],
        }
    )
    out = convert_currency(df, rates([("2024-07-01", "TWD", 32.0)]))
    assert len(out) == 1
    assert pd.isna(out["price_usd"].iloc[0])


def test_duplicate_rates_are_refused_rather_than_multiplying_reviews():
    """A duplicated (date, currency) pair would otherwise turn one review into
    two through the merge, inflating every aggregate downstream."""
    df = pd.DataFrame(
        {
            "review_date": pd.to_datetime(["2024-07-01"]),
            "price_currency": ["TWD"],
            "price_value": [500.0],
        }
    )
    dupes = rates([("2024-07-01", "TWD", 32.0), ("2024-07-01", "TWD", 31.0)])
    with pytest.raises(ValueError, match="row count"):
        convert_currency(df, dupes)


# --------------------------------------------------------------------------
# The review month's CPI
# --------------------------------------------------------------------------


def test_each_review_gets_the_cpi_for_its_month():
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2000-01-01"]), "price_usd": [10.0]}
    )
    cpi = cpi_table([(150.0, "2000-01-01"), (300.0, "2024-06-01")])
    out = attach_cpi(df, cpi)
    assert out["cpi"].iloc[0] == 150.0
    assert "date" not in out.columns  # the CPI table's key does not survive


def test_a_month_without_cpi_gets_no_cpi():
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2026-09-01"]), "price_usd": [25.0]}
    )
    out = attach_cpi(df, cpi_table([(300.0, "2024-06-01")]))
    assert len(out) == 1
    assert pd.isna(out["cpi"].iloc[0])
    assert out["price_usd"].iloc[0] == 25.0


def test_duplicate_cpi_months_are_refused():
    """A month listed twice would otherwise turn one review into two."""
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2000-01-01"]), "price_usd": [10.0]}
    )
    dupes = cpi_table([(150.0, "2000-01-01"), (151.0, "2000-01-01")])
    with pytest.raises(ValueError, match="row count"):
        attach_cpi(df, dupes)


def test_price_per_pound():
    df = pd.DataFrame({"price_usd": [20.0], "quantity_in_lbs": [0.5]})
    assert price_per_lb(df)["price_usd_per_lb"].iloc[0] == 40.0


def test_a_price_not_by_weight_has_no_price_per_pound():
    df = pd.DataFrame({"price_usd": [20.0], "quantity_in_lbs": [float("nan")]})
    assert pd.isna(price_per_lb(df)["price_usd_per_lb"].iloc[0])


# --------------------------------------------------------------------------
# The layer end to end
# --------------------------------------------------------------------------


def test_add_comparable_prices_runs_the_whole_chain():
    cleaned = pd.DataFrame(
        {
            "review_date": pd.to_datetime(["2000-01-01"]),
            "price_currency": ["USD"],
            "price_value": [20.0],
            "quantity_in_lbs": [1.0],
        }
    )
    out = add_comparable_prices(
        cleaned,
        exchange_rates=rates([("2000-01-01", "USD", 1.0)]),
        cpi=cpi_table([(150.0, "2000-01-01"), (300.0, "2024-06-01")]),
    )
    row = out.iloc[0]
    assert row["price_usd"] == 20.0
    assert row["price_usd_per_lb"] == 20.0
    assert row["cpi"] == 150.0
    # No baseline: that is applied where prices are shown.
    assert "price_usd_adj" not in out.columns
    assert "price_baseline_date" not in out.columns


def test_pricing_keeps_what_the_site_printed():
    """A priced row stays traceable back to the original figures."""
    cleaned = pd.DataFrame(
        {
            "review_date": pd.to_datetime(["2024-07-01"]),
            "price_currency": ["TWD"],
            "price_value": [500.0],
            "quantity_in_lbs": [0.5],
        }
    )
    out = add_comparable_prices(
        cleaned,
        exchange_rates=rates([("2024-07-01", "TWD", 32.0)]),
        cpi=cpi_table([(300.0, "2024-06-01"), (310.0, "2024-07-01")]),
    )
    assert out["price_value"].iloc[0] == 500.0
    assert out["price_currency"].iloc[0] == "TWD"


# --------------------------------------------------------------------------
# Whether a price can be put in baseline dollars
# --------------------------------------------------------------------------


def test_price_status_names_the_first_step_that_failed():
    nan = float("nan")
    df = pd.DataFrame(
        {
            "price_value": [nan, 10.0, 10.0, 10.0],
            "price_usd": [nan, nan, 10.0, 10.0],
            "cpi": [nan, nan, nan, 150.0],
        }
    )
    out = add_price_status(df)
    assert out["price_status"].tolist() == [
        "no_price",
        "no_exchange_rate",
        "no_cpi",
        "comparable",
    ]


def test_price_status_without_reference_data():
    df = pd.DataFrame({"price_value": [float("nan"), 10.0]})
    out = add_price_status(df)
    assert out["price_status"].tolist() == ["no_price", "not_converted"]
    assert set(out["price_status"]) <= set(PRICE_STATUSES)


# --------------------------------------------------------------------------
# Baseline dollars
# --------------------------------------------------------------------------


def test_a_price_is_expressed_in_the_baseline_months_dollars():
    """$10 in a month when CPI was 150, expressed in CPI-300 dollars, is $20."""
    cpi = cpi_table([(300.0, "2024-06-01"), (1000.0, "2025-06-01")])
    out = to_baseline_dollars(priced(10.0, 150.0, per_lb=20.0), cpi, date(2024, 6, 1))
    assert out["price_usd_adj"].iloc[0] == pytest.approx(20.0)
    assert out["price_usd_adj_per_lb"].iloc[0] == pytest.approx(40.0)
    assert out["price_baseline_date"].iloc[0] == pd.Timestamp("2024-06-01")


def test_the_baseline_follows_the_argument():
    cpi = cpi_table([(300.0, "2024-06-01"), (450.0, "2026-01-01")])
    a = to_baseline_dollars(priced(10.0, 150.0), cpi, date(2024, 6, 1))
    b = to_baseline_dollars(priced(10.0, 150.0), cpi, date(2026, 1, 1))
    assert a["price_usd_adj"].iloc[0] == 20.0  # 150 -> 300
    assert b["price_usd_adj"].iloc[0] == 30.0  # 150 -> 450
    assert a["price_baseline_date"].iloc[0] == pd.Timestamp("2024-06-01")
    assert b["price_baseline_date"].iloc[0] == pd.Timestamp("2026-01-01")


def test_a_row_without_cpi_gets_no_adjusted_price_and_no_baseline():
    """price_usd_adj holds baseline dollars only; price_usd keeps the price."""
    cpi = cpi_table([(300.0, "2024-06-01")])
    out = to_baseline_dollars(priced(25.0, float("nan")), cpi, date(2024, 6, 1))
    assert pd.isna(out["price_usd_adj"].iloc[0])
    assert pd.isna(out["price_baseline_date"].iloc[0])
    assert out["price_usd"].iloc[0] == 25.0


def test_a_row_without_a_price_gets_no_baseline():
    """The baseline is the unit of an adjusted price, so a review with no price
    has none."""
    cpi = cpi_table([(300.0, "2024-06-01")])
    out = to_baseline_dollars(priced(float("nan"), 150.0), cpi, date(2024, 6, 1))
    assert pd.isna(out["price_baseline_date"].iloc[0])


def test_a_baseline_the_cpi_table_does_not_cover_is_refused():
    """Picking a different baseline would change every price."""
    cpi = cpi_table([(300.0, "2024-06-01")])
    with pytest.raises(ValueError, match="covers through 2024-06-01"):
        to_baseline_dollars(priced(10.0, 150.0), cpi, date(2026, 1, 1))


def test_a_baseline_must_name_the_first_of_a_month():
    """CPI is monthly, so any other day has no CPI to adjust to."""
    cpi = cpi_table([(300.0, "2024-06-01")])
    with pytest.raises(ValueError, match="first day of a month"):
        to_baseline_dollars(priced(10.0, 150.0), cpi, date(2024, 6, 15))


def test_the_original_columns_are_kept():
    cpi = cpi_table([(300.0, "2024-06-01")])
    out = to_baseline_dollars(priced(10.0, 150.0, per_lb=20.0), cpi, date(2024, 6, 1))
    row = out.iloc[0]
    assert (row["price_usd"], row["price_usd_per_lb"], row["cpi"]) == (
        10.0,
        20.0,
        150.0,
    )
