"""Put every price in the same money, so they can be compared.

The site prints what a coffee cost in the currency and quantity of its day: NT$
per 225 grams in 2015, dollars per pound in 1997. Comparing those takes two
conversions and two reference tables the reviews do not carry -- historical
exchange rates, and the CPI.

:func:`add_comparable_prices` composes the steps; :func:`coffee.clean.clean_reviews`
calls it when both tables are available.
"""

from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

__all__ = [
    "DEFAULT_BASELINE_DATE",
    "add_comparable_prices",
    "convert_currency",
    "cpi_adjust_price",
    "load_cpi",
    "load_exchange_rates",
    "price_per_lb",
]

# The month whose dollars every adjusted price is expressed in. Changing it
# changes every price_usd_adj, so it is recorded on the output rather than left
# implicit.
DEFAULT_BASELINE_DATE = "2026-01-01"


# ==========================================================================
# Reference data
# ==========================================================================


def load_exchange_rates(path: Path) -> pd.DataFrame:
    """Flatten ``{date: {currency: rate}}`` into a (date, currency, rate) table.

    A lookup table rather than the nested mapping, so that conversion is a
    vectorised merge rather than a row-wise apply.
    """
    import json

    nested = json.loads(Path(path).read_text(encoding="utf-8"))
    return (
        pd.DataFrame(nested)
        .T.rename_axis("review_date")
        .reset_index()
        .melt(id_vars="review_date", var_name="price_currency", value_name="rate")
        .assign(review_date=lambda d: pd.to_datetime(d["review_date"]))
    )


def load_cpi(path: Path) -> pd.DataFrame:
    """Read the BLS CPI-U table into (date, cpi) rows, one per month."""
    cpi = pd.read_csv(path)
    cpi.columns = cpi.columns.str.strip().str.lower().str.replace(" ", "_")
    return (
        cpi.drop(columns=["half1", "half2"])
        .melt(id_vars="year", var_name="month", value_name="cpi")
        .assign(
            month=lambda d: d["month"].apply(
                lambda m: datetime.strptime(m, "%b").month
            ),
            date=lambda d: pd.to_datetime(d[["year", "month"]].assign(day=1)),
        )
        .drop(columns=["year", "month"])
    )


# ==========================================================================
# Steps
# ==========================================================================


def convert_currency(df: pd.DataFrame, exchange_rates: pd.DataFrame) -> pd.DataFrame:
    """Convert prices to USD at the rate for the review's month.

    Checks that the row count survives the merge, since a duplicated
    (date, currency) pair in the rate table would otherwise multiply reviews.
    """
    before = len(df)
    merged = df.merge(exchange_rates, on=["review_date", "price_currency"], how="left")
    if len(merged) != before:
        raise ValueError(
            f"Exchange-rate merge changed the row count ({before} -> {len(merged)}); "
            "the rate table likely holds duplicate (date, currency) pairs."
        )
    return merged.assign(
        price_usd=lambda d: (d["price_value"] / d["rate"]).round(2)
    ).drop(columns="rate")


def cpi_adjust_price(
    df: pd.DataFrame, cpi: pd.DataFrame, baseline_date: str = DEFAULT_BASELINE_DATE
) -> pd.DataFrame:
    """Express prices in `baseline_date` dollars using CPI-U.

    Where CPI is unavailable, typically for the current month, the unadjusted
    USD price is kept rather than dropped.
    """
    baseline = cpi.loc[cpi["date"] == baseline_date, "cpi"]
    if baseline.empty:
        # Naming the range turns this into a one-step fix: either the baseline
        # is a typo, or the CPI table needs a newer download from the BLS.
        published = cpi.dropna(subset=["cpi"])["date"]
        latest = published.max().date() if not published.empty else "none"
        raise ValueError(
            f"No CPI value for baseline date {baseline_date!r}. "
            f"The CPI table covers through {latest}. Either pass a "
            "--baseline-date within that range, or download a newer table "
            "from https://www.bls.gov/cpi/data.htm."
        )

    before = len(df)
    merged = df.merge(cpi, how="left", left_on="review_date", right_on="date")
    if len(merged) != before:
        raise ValueError(
            f"CPI merge changed the row count ({before} -> {len(merged)})."
        )
    return merged.assign(
        price_usd_adj=lambda d: np.where(
            d["cpi"].isna(),
            d["price_usd"],
            (d["price_usd"] * baseline.iloc[0] / d["cpi"]).round(2),
        )
    )


def price_per_lb(df: pd.DataFrame) -> pd.DataFrame:
    """The comparable figure: inflation-adjusted USD per pound."""
    return df.assign(
        price_usd_adj_per_lb=lambda d: np.round(
            d["price_usd_adj"] / d["quantity_in_lbs"], 2
        )
    )


# ==========================================================================
# The layer
# ==========================================================================


def add_comparable_prices(
    cleaned: pd.DataFrame,
    *,
    exchange_rates: pd.DataFrame,
    cpi: pd.DataFrame,
    baseline_date: str = DEFAULT_BASELINE_DATE,
) -> pd.DataFrame:
    """Cleaned reviews in, comparable prices out.

    Adds ``price_usd``, ``price_usd_adj`` and ``price_usd_adj_per_lb``. The
    figures the cleaned layer already carries -- ``price_value``,
    ``price_currency``, ``quantity_in_lbs`` -- are left untouched, so a
    priced row stays traceable back to what the site printed.
    """
    return (
        cleaned.pipe(convert_currency, exchange_rates)
        .pipe(cpi_adjust_price, cpi, baseline_date)
        .pipe(price_per_lb)
    )
