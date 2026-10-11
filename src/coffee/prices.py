"""Put every price in the same money, so they can be compared.

The site prints what a coffee cost in the currency and quantity of its day: NT$
per 225 grams, dollars per pound, Canadian dollars per 340 grams. Comparing those
takes two conversions and two reference tables the reviews do not carry -- historical
exchange rates, and the CPI.

The conversions happen in two places:

- :func:`add_comparable_prices` runs during cleaning. It converts each price to
  US dollars at the review month's rate and attaches that month's CPI. These
  are facts about the review, so they belong in the cleaned dataset.
- :func:`to_baseline_dollars` runs wherever prices are shown. It expresses
  prices in the dollars of a chosen month. The baseline is a choice about how
  to show prices, not a fact about a review, so the cleaned dataset carries
  none, and a notebook or dashboard can pick any month the CPI table covers.
"""

import json
from datetime import date, datetime
from pathlib import Path
from typing import Final

import numpy as np
import pandas as pd

__all__ = [
    "PRICE_STATUSES",
    "add_comparable_prices",
    "add_price_status",
    "attach_cpi",
    "convert_currency",
    "load_cpi",
    "load_exchange_rates",
    "price_per_lb",
    "to_baseline_dollars",
]

# Whether a review's price can be put in a baseline month's dollars, and if
# not, why: the first step that failed, in pipeline order.
PRICE_STATUSES: Final = (
    "no_price",  # the page names no price
    "not_converted",  # cleaned without exchange rates and CPI
    "no_exchange_rate",  # no rate for the currency in the review month
    "no_cpi",  # no published CPI for the review month
    "comparable",  # has price_usd and cpi, so any baseline can be applied
)

# ==========================================================================
# Reference data
# ==========================================================================


def load_exchange_rates(path: Path) -> pd.DataFrame:
    """Flatten ``{date: {currency: rate}}`` into a (date, currency, rate) table.

    A lookup table rather than the nested mapping, so that conversion is a
    vectorized merge rather than a row-wise apply.
    """
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


def attach_cpi(df: pd.DataFrame, cpi: pd.DataFrame) -> pd.DataFrame:
    """Add ``cpi``: the CPI-U for each review's month.

    Empty where the BLS has published no CPI for the month. Checks that the row
    count survives the merge, since a duplicated month in the CPI table would
    otherwise multiply reviews.
    """
    before = len(df)
    merged = df.merge(cpi, how="left", left_on="review_date", right_on="date")
    if len(merged) != before:
        raise ValueError(
            f"CPI merge changed the row count ({before} -> {len(merged)}); "
            "the CPI table likely holds a month twice."
        )
    # `date` came in on the merge and duplicates review_date exactly.
    return merged.drop(columns="date")


def price_per_lb(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``price_usd_per_lb``: US dollars per pound, for weight prices."""
    return df.assign(
        price_usd_per_lb=lambda d: np.round(d["price_usd"] / d["quantity_in_lbs"], 2)
    )


# ==========================================================================
# The layer
# ==========================================================================


def add_price_status(df: pd.DataFrame) -> pd.DataFrame:
    """Add ``price_status``: whether a price can be put in baseline dollars.

    One of :data:`PRICE_STATUSES`. It answers the question for every review,
    so a missing price can be told apart from one that could not be converted.
    Whether a price per pound exists is a separate question, which
    ``price_format`` answers: only ``weight`` prices have one.
    """
    status = pd.Series("comparable", index=df.index, dtype="string")
    if "price_usd" in df.columns:
        status = status.mask(df["cpi"].isna(), "no_cpi")
        status = status.mask(df["price_usd"].isna(), "no_exchange_rate")
    else:
        status[:] = "not_converted"
    status = status.mask(df["price_value"].isna(), "no_price")
    return df.assign(price_status=status)


def add_comparable_prices(
    cleaned: pd.DataFrame, *, exchange_rates: pd.DataFrame, cpi: pd.DataFrame
) -> pd.DataFrame:
    """Cleaned reviews in, prices ready for any baseline out.

    Adds ``price_usd``, ``price_usd_per_lb`` and ``cpi``. The figures the
    cleaned layer already carries -- ``price_value``, ``price_currency``,
    ``quantity_in_lbs`` -- are left untouched, so a priced row stays traceable
    back to what the site printed.
    """
    return (
        cleaned.pipe(convert_currency, exchange_rates)
        .pipe(attach_cpi, cpi)
        .pipe(price_per_lb)
    )


# ==========================================================================
# Baseline dollars, applied where prices are shown
# ==========================================================================


def to_baseline_dollars(
    df: pd.DataFrame, cpi: pd.DataFrame, baseline: date
) -> pd.DataFrame:
    """Express prices in the dollars of the month ``baseline``.

    Takes cleaned reviews, which carry ``price_usd``, ``price_usd_per_lb`` and
    the review month's ``cpi``, and the CPI table from :func:`load_cpi`. Adds
    ``price_usd_adj`` and ``price_usd_adj_per_lb``, each scaled by
    ``CPI(baseline) / cpi``, and ``price_baseline_date``. The baseline travels
    with the number because it is the number's unit.

    A row without ``cpi`` or ``price_usd`` gets no adjusted price and no
    baseline. ``baseline`` must be the first day of a month the CPI table
    covers.
    """
    if baseline.day != 1:
        raise ValueError(
            f"Baseline {baseline.isoformat()} must be the first day of a month, "
            f"such as {baseline.replace(day=1).isoformat()}: CPI is monthly."
        )
    baseline_cpi = cpi.loc[cpi["date"] == pd.Timestamp(baseline), "cpi"].dropna()
    if baseline_cpi.empty:
        # Naming the range turns this into a one-step fix: either the baseline
        # is a typo, or the CPI table needs updating with `fetch-cpi`.
        published = cpi.dropna(subset=["cpi"])["date"]
        latest = published.max().date() if not published.empty else "none"
        raise ValueError(
            f"No CPI value for baseline {baseline.isoformat()}. The CPI table "
            f"covers through {latest}. Choose a baseline within that range, or "
            "update the table with `uv run fetch-cpi`."
        )

    factor = baseline_cpi.iloc[0] / df["cpi"]
    adjusted = (df["price_usd"] * factor).round(2)
    return df.assign(
        price_usd_adj=adjusted,
        price_usd_adj_per_lb=(df["price_usd_per_lb"] * factor).round(2),
        # Only where the adjustment ran: not on a month without CPI, and not on
        # a review with no price at all.
        price_baseline_date=pd.Series(pd.Timestamp(baseline), index=df.index).where(
            adjusted.notna()
        ),
    )
