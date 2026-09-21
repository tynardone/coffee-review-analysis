"""Tests for the price conversions.

These conversions decide what every price comparison means, and a bug in them
is invisible: a wrong rate or a missing CPI month does not raise, it quietly
changes a conclusion.
"""

import pandas as pd
import pytest

from coffee.prices import (
    add_comparable_prices,
    convert_currency,
    cpi_adjust_price,
    price_per_lb,
)


def rates(rows):
    return pd.DataFrame(rows, columns=["review_date", "price_currency", "rate"]).assign(
        review_date=lambda d: pd.to_datetime(d["review_date"])
    )


def cpi_table(rows):
    return pd.DataFrame(rows, columns=["cpi", "date"]).assign(
        date=lambda d: pd.to_datetime(d["date"])
    )


# --------------------------------------------------------------------------
# Currency and inflation
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


def test_inflation_adjustment_uses_the_baseline_month():
    """$10 in a month when CPI was 150, expressed in CPI-300 dollars, is $20."""
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2000-01-01"]), "price_usd": [10.0]}
    )
    cpi = cpi_table([(150.0, "2000-01-01"), (300.0, "2024-06-01")])
    out = cpi_adjust_price(df, cpi, baseline_date="2024-06-01")
    assert out["price_usd_adj"].iloc[0] == pytest.approx(20.0)


def test_a_month_without_cpi_keeps_the_unadjusted_price():
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2026-09-01"]), "price_usd": [25.0]}
    )
    cpi = cpi_table([(300.0, "2024-06-01")])
    out = cpi_adjust_price(df, cpi, baseline_date="2024-06-01")
    assert out["price_usd_adj"].iloc[0] == 25.0


def test_an_unknown_baseline_date_is_an_error():
    """Silently picking a different baseline would change every price."""
    cpi = cpi_table([(300.0, "2024-06-01")])
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2024-06-01"]), "price_usd": [1.0]}
    )
    with pytest.raises(ValueError, match="baseline date"):
        cpi_adjust_price(df, cpi, baseline_date="1970-01-01")


def test_price_per_pound():
    df = pd.DataFrame({"price_usd_adj": [20.0], "quantity_in_lbs": [0.5]})
    assert price_per_lb(df)["price_usd_adj_per_lb"].iloc[0] == 40.0


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
        baseline_date="2024-06-01",
    )
    row = out.iloc[0]
    assert row["price_usd"] == 20.0
    assert row["price_usd_adj"] == 40.0  # CPI 150 -> 300
    assert row["price_usd_adj_per_lb"] == 40.0


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
        baseline_date="2024-06-01",
    )
    assert out["price_value"].iloc[0] == 500.0
    assert out["price_currency"].iloc[0] == "TWD"


# --------------------------------------------------------------------------
# The baseline travels with the number
# --------------------------------------------------------------------------


def test_the_baseline_is_recorded_on_every_adjusted_row():
    """Without it, two files in different dollars look identical."""
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2000-01-01"]), "price_usd": [10.0]}
    )
    cpi = cpi_table([(150.0, "2000-01-01"), (300.0, "2024-06-01")])
    out = cpi_adjust_price(df, cpi, baseline_date="2024-06-01")
    assert out["price_baseline_date"].iloc[0] == "2024-06-01"


def test_a_month_without_cpi_gets_no_baseline():
    """Its price was never adjusted, so claiming a baseline would be a lie."""
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2026-09-01"]), "price_usd": [25.0]}
    )
    cpi = cpi_table([(300.0, "2024-06-01")])
    out = cpi_adjust_price(df, cpi, baseline_date="2024-06-01")
    assert out["price_usd_adj"].iloc[0] == 25.0  # unadjusted
    assert pd.isna(out["price_baseline_date"].iloc[0])


def test_the_baseline_follows_the_argument():
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2000-01-01"] * 2), "price_usd": [10.0, 10.0]}
    )
    cpi = cpi_table(
        [(150.0, "2000-01-01"), (300.0, "2024-06-01"), (450.0, "2026-01-01")]
    )
    a = cpi_adjust_price(df, cpi, baseline_date="2024-06-01")
    b = cpi_adjust_price(df, cpi, baseline_date="2026-01-01")
    assert a["price_usd_adj"].iloc[0] == 20.0  # 150 -> 300
    assert b["price_usd_adj"].iloc[0] == 30.0  # 150 -> 450
    assert a["price_baseline_date"].iloc[0] == "2024-06-01"
    assert b["price_baseline_date"].iloc[0] == "2026-01-01"


def test_the_cpi_tables_own_columns_do_not_survive_the_merge():
    """`date` duplicated review_date and `cpi` was only an input."""
    df = pd.DataFrame(
        {"review_date": pd.to_datetime(["2000-01-01"]), "price_usd": [10.0]}
    )
    out = cpi_adjust_price(
        df, cpi_table([(150.0, "2000-01-01"), (300.0, "2024-06-01")]), "2024-06-01"
    )
    assert "cpi" not in out.columns
    assert "date" not in out.columns
