"""Tests for the cleaning layer.

This code decides what every downstream number means, and until now none of it
was tested — it lived in a notebook. The cases chosen here are the ones where a
bug is INVISIBLE: a wrong exchange rate or a silently dropped row does not
raise, it just quietly changes a conclusion.
"""

import numpy as np
import pandas as pd
import pytest

from coffee.clean import (
    CURRENCY_MAP,
    apply_roaster_crosswalk,
    check_raw_schema,
    clean_currency,
    clean_origin,
    clean_reviews,
    clean_roaster_location,
    convert_currency,
    convert_to_lbs,
    cpi_adjust_price,
    normalise_types,
    price_per_lb,
    split_price_and_quantity,
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
# Column and type normalisation
# --------------------------------------------------------------------------


def test_check_raw_schema_passes_normalised_columns_through():
    df = pd.DataFrame(columns=["roaster_location", "est_price", "acidity/structure"])
    assert check_raw_schema(df) is df


def test_check_raw_schema_rejects_unnormalised_columns():
    """Data that predates parse-time naming must fail here, not deep in a merge."""
    df = pd.DataFrame(columns=["roaster_location", "Est. Price", "review_date"])
    with pytest.raises(ValueError, match="not normalised"):
        check_raw_schema(df)


def base_frame(**overrides):
    row = {
        "review_date": "July 2024",
        "acidity": "8",
        "acidity/structure": np.nan,
        "agtron": "57/80",
        "title": "Ethiopia Natural",
        "with_milk": np.nan,
        "rating": "93",
        "aroma": "9",
        "body": "9",
        "flavor": "9",
        "aftertaste": "8",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_agtron_splits_into_two_readings():
    out = normalise_types(base_frame())
    assert out["agtron_external"].iloc[0] == 57
    assert out["agtron_ground"].iloc[0] == 80
    assert "agtron" not in out.columns


def test_a_single_agtron_reading_leaves_ground_missing():
    out = normalise_types(base_frame(agtron="57"))
    assert out["agtron_external"].iloc[0] == 57
    assert pd.isna(out["agtron_ground"].iloc[0])


def test_acidity_falls_back_to_the_renamed_column():
    """The site renamed `acidity` to `acidity/structure` across 2017-18; the
    cleaned layer carries one column holding whichever was present."""
    out = normalise_types(base_frame(acidity=np.nan, **{"acidity/structure": "7"}))
    assert out["acidity"].iloc[0] == 7


def test_agtron_typos_are_dropped():
    """Readings above 100 are website typos, not measurements."""
    df = pd.concat([base_frame(agtron="57/80"), base_frame(agtron="571/80")])
    assert len(normalise_types(df)) == 1


def test_espresso_is_flagged_from_title_or_milk_score():
    assert normalise_types(base_frame(title="Espresso Blend"))["is_espresso"].iloc[0]
    assert normalise_types(base_frame(with_milk="9"))["is_espresso"].iloc[0]
    assert not normalise_types(base_frame())["is_espresso"].iloc[0]


def test_qualitative_scores_become_missing_not_an_error():
    """A few rows carry text like 'Very Low' where a number belongs."""
    out = normalise_types(base_frame(acidity="Very Low"))
    assert pd.isna(out["acidity"].iloc[0])


# --------------------------------------------------------------------------
# Price parsing
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, value, currency, qty_value, qty_unit",
    [
        ("$19.00/16 ounces", 19.0, "$", 16.0, "ounces"),
        ("NT $500/227 grams", 500.0, "NT $", 227.0, "grams"),
        ("$45.00/1 kg", 45.0, "$", 1.0, "kilograms"),
        ("$1,200.00/12 ounces", 1200.0, "$", 12.0, "ounces"),
    ],
)
def test_price_and_quantity_are_split_out(raw, value, currency, qty_value, qty_unit):
    out = split_price_and_quantity(pd.DataFrame({"est_price": [raw]}))
    assert out["price_value"].iloc[0] == value
    assert out["price_currency"].iloc[0] == currency
    assert out["quantity_value"].iloc[0] == qty_value
    assert out["quantity_unit"].iloc[0] == qty_unit


def test_non_whole_bean_formats_keep_the_row_but_lose_the_quantity():
    """THE ROW-COUNT INVARIANT.

    Capsules and pods are not sold by weight, so their price per pound is
    meaningless — but the review itself is still data. The merge is a LEFT
    join precisely so this never removes a review.
    """
    df = pd.DataFrame({"est_price": ["$19.00/16 ounces", "$30.00/10 capsules"]})
    out = split_price_and_quantity(df)
    assert len(out) == 2
    assert out["quantity_value"].iloc[0] == 16.0
    assert pd.isna(out["quantity_value"].iloc[1])


def test_an_unparseable_price_keeps_the_row():
    out = split_price_and_quantity(pd.DataFrame({"est_price": [np.nan]}))
    assert len(out) == 1
    assert pd.isna(out["price_value"].iloc[0])


@pytest.mark.parametrize("code", sorted(set(CURRENCY_MAP.values())))
def test_every_mapped_currency_reaches_an_iso_code(code):
    symbols = [k for k, v in CURRENCY_MAP.items() if v == code]
    out = clean_currency(pd.DataFrame({"price_currency": symbols}))
    assert set(out["price_currency"]) == {code}


def test_an_unmapped_currency_passes_through_uppercased():
    """Better a visible unknown code than a silent coercion to USD."""
    out = clean_currency(pd.DataFrame({"price_currency": ["zzz"]}))
    assert out["price_currency"].iloc[0] == "ZZZ"


@pytest.mark.parametrize(
    "unit, value, lbs",
    [
        ("pounds", 1, 1.0),
        ("ounces", 16, 1.0),
        ("kilograms", 1, 2.2),
        ("grams", 454, 1.0),
    ],
)
def test_quantities_convert_to_pounds(unit, value, lbs):
    df = pd.DataFrame({"quantity_value": [value], "quantity_unit": [unit]})
    assert convert_to_lbs(df)["quantity_in_lbs"].iloc[0] == pytest.approx(lbs, abs=0.01)


def test_an_unknown_unit_yields_no_weight_rather_than_a_wrong_one():
    df = pd.DataFrame({"quantity_value": [1], "quantity_unit": ["furlongs"]})
    assert pd.isna(convert_to_lbs(df)["quantity_in_lbs"].iloc[0])


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
# Places
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Yirgacheffe, Ethiopia", "ethiopia"),
        ("Kenya; Brazil", "brazil;kenya"),
        ("Guji Zone, Oromia Region, southern Ethiopia", "ethiopia"),
    ],
)
def test_origin_countries_are_extracted(text, expected):
    out = clean_origin(pd.DataFrame({"coffee_origin": [text]}))
    assert out["origin_country"].iloc[0] == expected


def test_an_unmatched_origin_falls_back_to_its_text():
    """Visible and reconcilable beats blank."""
    out = clean_origin(pd.DataFrame({"coffee_origin": ["Somewhere Unnamed"]}))
    assert out["origin_country"].iloc[0] == "somewhere unnamed"


@pytest.mark.parametrize(
    "location, country, state",
    [
        ("Portland, Oregon", "united states", "oregon"),
        ("Taipei, Taiwan", "taiwan", ""),
        ("Kailua-Kona, Hawai'i", "united states", "hawaii"),
        ("Bozeman, Montana.", "united states", "montana"),
        ("Scottsdale Arizona", "united states", "arizona"),
        ("London, England", "united kingdom", ""),
        ("Seoul, South Korea", "korea, republic of", ""),
    ],
)
def test_roaster_locations_resolve(location, country, state):
    out = clean_roaster_location(pd.DataFrame({"roaster_location": [location]}))
    assert out["roaster_country"].iloc[0] == country
    assert out["roaster_us_state"].iloc[0] == state


def test_a_missing_roaster_location_is_blank_not_an_error():
    out = clean_roaster_location(pd.DataFrame({"roaster_location": [np.nan]}))
    assert out["roaster_country"].iloc[0] == ""


# --------------------------------------------------------------------------
# Roaster crosswalk
# --------------------------------------------------------------------------


def test_the_crosswalk_adds_a_canonical_name_without_losing_the_original():
    """The cleaned layer adds to what was scraped rather than overwriting it,
    so a wrong merge stays traceable to its source spelling."""
    df = pd.DataFrame({"roaster": ["Boyd Coffee", "Boyds Coffee"]})
    crosswalk = pd.DataFrame(
        {
            "raw_name": ["Boyd Coffee", "Boyds Coffee"],
            "canonical_name": ["Boyds Coffee", "Boyds Coffee"],
        }
    )
    out = apply_roaster_crosswalk(df, crosswalk)
    assert out["roaster_canonical"].tolist() == ["Boyds Coffee", "Boyds Coffee"]
    assert out["roaster"].tolist() == ["Boyd Coffee", "Boyds Coffee"]


def test_a_roaster_absent_from_the_crosswalk_keeps_its_own_name():
    df = pd.DataFrame({"roaster": ["Unlisted Roasters"]})
    crosswalk = pd.DataFrame({"raw_name": ["Other"], "canonical_name": ["Other"]})
    out = apply_roaster_crosswalk(df, crosswalk)
    assert out["roaster_canonical"].iloc[0] == "Unlisted Roasters"


# --------------------------------------------------------------------------
# The layer end to end
# --------------------------------------------------------------------------


def test_clean_reviews_runs_the_whole_chain():
    raw = pd.DataFrame(
        [
            {
                "rating": "93",
                "roaster": "Boyd Coffee",
                "roaster_location": "Portland, Oregon",
                "title": "Ethiopia Natural",
                "coffee_origin": "Yirgacheffe, Ethiopia",
                "agtron": "57/80",
                "est_price": "$20.00/16 ounces",
                "review_date": "January 2000",
                "acidity": "8",
                "acidity/structure": np.nan,
                "aroma": "9",
                "body": "9",
                "flavor": "9",
                "aftertaste": "8",
                "with_milk": np.nan,
            }
        ]
    )
    out = clean_reviews(
        raw,
        exchange_rates=rates([("2000-01-01", "USD", 1.0)]),
        cpi=cpi_table([(150.0, "2000-01-01"), (300.0, "2024-06-01")]),
        crosswalk=pd.DataFrame(
            {"raw_name": ["Boyd Coffee"], "canonical_name": ["Boyds Coffee"]}
        ),
        baseline_date="2024-06-01",
    )
    row = out.iloc[0]
    assert row["price_usd"] == 20.0
    assert row["price_usd_adj"] == 40.0  # CPI 150 -> 300
    assert row["price_usd_adj_per_lb"] == 40.0  # 16 ounces = 1 lb
    assert row["origin_country"] == "ethiopia"
    assert row["roaster_us_state"] == "oregon"
    assert row["roaster_canonical"] == "Boyds Coffee"
    assert row["agtron_external"] == 57
