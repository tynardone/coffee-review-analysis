"""Turn scraped reviews into the cleaned layer.

Raw rows record what the site published; cleaned rows are what the analysis
consumes. This module owns that transition: coercing types, parsing the
free-text price into a value, a currency and a quantity, and resolving origin
and roaster locations to countries.

The field-level work needs nothing but the raw scrape. Putting prices in
comparable money needs historical exchange rates and CPI; those steps live in
:mod:`coffee.enrich`, and :func:`clean_reviews` applies them when both are
passed. They are optional so that this module still runs on a fresh checkout
before any reference data has been fetched.

Every step is a pure ``DataFrame -> DataFrame`` function, and every piece of
reference data is passed in rather than read from disk, so the transformation
can be tested against a few hand-written rows.

:func:`clean_reviews` composes the steps in order. The individual steps remain
public because they are useful one at a time when exploring in a notebook.
"""

import re
from functools import cache

import numpy as np
import pandas as pd
import pycountry
from unidecode import unidecode

from coffee.enrich import DEFAULT_BASELINE_DATE, enrich_reviews
from coffee.parser import normalise_field_name

__all__ = [
    "CURRENCY_MAP",
    "DEFAULT_BASELINE_DATE",
    "DEFAULT_MAX_AGTRON",
    "NON_WHOLE_BEAN_TERMS",
    "US_PRICE_UNITS",
    "apply_roaster_crosswalk",
    "check_raw_schema",
    "clean_currency",
    "clean_origin",
    "clean_reviews",
    "clean_roaster_location",
    "convert_to_lbs",
    "normalise_types",
    "split_price_and_quantity",
]

# Agtron readings above this are website typos, not measurements.
DEFAULT_MAX_AGTRON = 100

# Formats that are not whole-bean coffee sold by weight. Their prices are not
# comparable per pound, so the quantity is left unparsed rather than guessed.
NON_WHOLE_BEAN_TERMS: list[str] = [
    "can",
    "box",
    "capsules",
    "K-",
    "cups",
    "bags",
    "concentrate",
    "discs",
    "bottle",
    "pods",
    "ml",
    "pouch",
    "packet|tin",
    "instant",
    "sachet",
    "vue",
    "single-serve",
    "fluid",
    "capsultes",
]

# Currency symbols and aliases the site uses, mapped to ISO 4217. Matched
# against the whole value after stripping "$", since exact matching avoids the
# fragility of substring replacement.
CURRENCY_MAP: dict[str, str] = {
    "": "USD",
    "US": "USD",
    "PRICE:": "USD",
    "#": "GBP",
    "£": "GBP",
    "POUND": "GBP",
    "¥": "JPY",
    "€": "EUR",
    "E": "EUR",
    "EUROS": "EUR",
    "PESOS": "MXN",
    "RMB": "CNY",
    "RM": "MYR",
    "NT": "TWD",
    "NTD": "TWD",
    "HK": "HKD",
}

US_PRICE_UNITS: dict[str, float] = {
    "ounces": 1 / 16,
    "pounds": 1,
    "kilograms": 2.20462,
    "grams": 0.00220462,
}

# Country names the site uses that pycountry does not match on its own.
COUNTRY_ALIASES: dict[str, str] = {
    "south korea": "korea, republic of",
    "north korea": "korea, democratic people's republic of",
    "england": "united kingdom",
    "scotland": "united kingdom",
    "wales": "united kingdom",
    "russia": "russian federation",
    "czech republic": "czechia",
    "slovak republic": "slovakia",
    "the netherlands": "netherlands",
    "holland": "netherlands",
    "vietnam": "viet nam",
    "british colombia": "canada",
    "british columbia": "canada",
}

_NUMERIC_COLUMNS = [
    "agtron_external",
    "agtron_ground",
    "acidity",
    "rating",
    "aroma",
    "body",
    "flavor",
    "aftertaste",
]


# ==========================================================================
# Reference data
# ==========================================================================


@cache
def _country_pattern() -> re.Pattern[str]:
    """Alternation over country names, longest first so multi-word names win.

    Built once and cached, since assembling it from pycountry on every call
    would dominate the runtime of origin matching.
    """
    names = {unidecode(c.name.lower()) for c in pycountry.countries}
    for drop in [
        "american samoa",
        "united states minor outlying islands",
        "south sudan",
        "south georgia and the south sandwich islands",
        "british indian ocean territory",
        "congo, the democratic republic of the",
        "taiwan, province of china",
        "guinea",
    ]:
        names.discard(drop)
    names = {n.split(",")[0] for n in names} | {"taiwan"}
    ordered = sorted(map(re.escape, names), key=len, reverse=True)
    return re.compile(r"\b(" + "|".join(ordered) + r")\b")


@cache
def _us_states() -> frozenset[str]:
    states = {
        unidecode(s.name.lower())
        for s in pycountry.subdivisions.get(country_code="US") or []
    }
    return frozenset(states | {"district of columbia", "washington dc", "dc"})


# ==========================================================================
# Steps
# ==========================================================================


def check_raw_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Reject raw data whose column names were never normalised.

    Field names are fixed at the scrape boundary by
    :func:`coffee.parser.normalise_field_name`, so the cleaning layer operates
    on data rather than on labels. A file predating that, carrying
    ``"est. price"`` where ``est_price`` belongs, would otherwise fail several
    steps later with a ``KeyError`` naming a column the caller never wrote.
    """
    stale = [c for c in df.columns if c != normalise_field_name(str(c))]
    if stale:
        raise ValueError(
            f"{len(stale)} column(s) are not normalised: {sorted(stale)[:5]}. "
            "This file predates field-name normalisation at parse time; "
            "re-scrape it, or rename the columns with "
            "coffee.parser.normalise_field_name first."
        )
    return df


def _agtron_parts(df: pd.DataFrame) -> pd.DataFrame:
    """Agtron as (external, ground), with both columns guaranteed to exist."""
    return (
        df["agtron"]
        .astype("string")
        .str.split("/", n=1, expand=True)
        .reindex(columns=[0, 1])
        # reindex fills a created column with float NaN, which has no .str
        .astype("string")
    )


def normalise_types(
    df: pd.DataFrame, max_agtron: int = DEFAULT_MAX_AGTRON
) -> pd.DataFrame:
    """Parse dates, split agtron, coalesce acidity, coerce scores to numbers.

    Also drops rows whose agtron exceeds `max_agtron`, those readings being
    site typos. :func:`clean_reviews` reports how many were removed, since a
    step that changes the row count should not do so silently.
    """
    return (
        df.assign(
            review_date=lambda d: pd.to_datetime(d["review_date"], format="%B %Y"),
            # One field under two names; the site renamed it across 2017-18.
            acidity=lambda d: d["acidity"].fillna(d["acidity/structure"]),
            # reindex: a batch in which no agtron carries a "/" produces a
            # single split column, and indexing [1] would raise.
            agtron_external=lambda d: pd.to_numeric(
                _agtron_parts(d)[0].str.strip(), errors="coerce"
            ),
            agtron_ground=lambda d: pd.to_numeric(
                _agtron_parts(d)[1].str.strip(), errors="coerce"
            ),
            is_espresso=lambda d: (
                d["title"].str.contains("espresso", case=False, na=False)
                | d["with_milk"].notna()
            ),
        )
        .replace(["", "NR", "N/A", "na"], np.nan)
        # fillna(False): a missing agtron is not a typo. Without it a null
        # reading makes the comparison NA, which propagates through `~` and
        # drops the row rather than keeping it.
        .loc[
            lambda d: (
                ~(
                    (d["agtron_external"] > max_agtron)
                    | (d["agtron_ground"] > max_agtron)
                ).fillna(False)
            )
        ]
        .map(lambda x: x.strip() if isinstance(x, str) else x)
        # errors="ignore": the scraped schema varies with a page's vintage, so
        # an absent column is expected rather than an error.
        .drop(columns=["acidity/structure", "agtron"], errors="ignore")
        .assign(
            **{
                col: lambda d, col=col: pd.to_numeric(d[col], errors="coerce")
                for col in _NUMERIC_COLUMNS
            }
        )
    )


def split_price_and_quantity(df: pd.DataFrame) -> pd.DataFrame:
    """Parse ``est_price`` ("$19.00/16 ounces") into value, currency, quantity.

    Rows whose quantity names a non-whole-bean format, or that cannot be
    parsed, keep every other field and receive no quantity. The result is
    merged back on the index with a left join, so no review is removed.
    """
    drop_terms = "|".join(NON_WHOLE_BEAN_TERMS)
    parsed = (
        df["est_price"]
        .astype("string")
        .str.split("/", n=1, expand=True)
        # Both halves are guaranteed to exist: a batch with no "/" anywhere
        # would otherwise yield one column and lose `quantity`.
        .reindex(columns=[0, 1])
        .astype("string")
        .replace(",", "", regex=True)
        .rename(columns={0: "price", 1: "quantity"})
        .assign(
            quantity=lambda d: (
                d["quantity"]
                .str.lower()
                .str.strip()
                .str.replace(r"\(.*?\)", "", regex=True)
                .str.replace(r";.*", "", regex=True)
                # Kilograms are expanded first. The ".g$" rule below is meant
                # for "250g" but also matches "kg", which would read "1 kg" as
                # 1 gram. Rows written "1 kg." escape only via the trailing
                # period, so the ordering here is what makes the rule safe.
                .str.replace("kilogram", "kilograms")
                .str.replace("kg", "kilograms")
                .str.replace(r".g$", " grams", regex=True)
                .str.replace(r"\sg$", "grams", regex=True)
                .str.replace(r"\bgram$", "grams", regex=True)
                .str.replace(r"pound$", "1 pounds", regex=True)
                .str.replace(r"oz|onces|ouncues|ounce$|ounces\*", "ounces", regex=True)
                .str.replace("online", "")
                .str.strip()
            ),
            price=lambda d: d["price"].str.replace("..", "."),
        )
        .dropna()
        .loc[lambda d: ~d["quantity"].str.contains(drop_terms, case=False)]
        .assign(
            quantity_value=lambda d: (
                d["quantity"].str.extract(r"(\d+(?:\.\d+)?)").astype(float)
            ),
            quantity_unit=lambda d: (
                d["quantity"]
                .str.replace(r"(\d+)", "", regex=True)
                .replace(r"\.", "", regex=True)
                .str.strip()
                .mask(lambda s: s == "g", "grams")
                .mask(lambda s: s == "kilo", "kilograms")
                .str.strip()
            ),
            price_value=lambda d: (
                d["price"].str.extract(r"(\d+\.\d+|\d+)").astype(float)
            ),
            price_currency=lambda d: (
                d["price"]
                .str.replace(",", "")
                .str.replace(r"(\d+\.\d+|\d+)", "", regex=True)
                .str.strip()
            ),
        )
        .drop(columns=["price", "quantity"])
        .loc[lambda d: ~d["quantity_unit"].str.contains(r"\(", regex=True)]
    )
    return df.merge(parsed, how="left", left_index=True, right_index=True)


def convert_to_lbs(df: pd.DataFrame) -> pd.DataFrame:
    """Express every quantity in pounds so prices can be compared per unit."""
    return df.assign(
        quantity_in_lbs=lambda d: np.round(
            d["quantity_value"] * d["quantity_unit"].map(US_PRICE_UNITS), 2
        )
    )


def clean_currency(df: pd.DataFrame) -> pd.DataFrame:
    """Standardise the currency column to ISO 4217 codes."""
    return df.assign(
        price_currency=lambda d: (
            d["price_currency"]
            .str.upper()
            .str.replace("$", "", regex=False)
            .str.strip()
            .replace(CURRENCY_MAP)
        )
    )


def clean_origin(df: pd.DataFrame) -> pd.DataFrame:
    """Extract origin countries from the coffee_origin text.

    Falls back to the original text when no country matches, so that an
    unresolved origin stays visible for manual reconciliation rather than
    becoming blank.
    """
    pattern = _country_pattern()
    origin = df["coffee_origin"].str.lower()

    def match(text: str) -> str:
        if pd.isna(text) or text == "":
            return ""
        found = pattern.findall(text)
        return ";".join(sorted(set(found))) if found else text

    return df.assign(coffee_origin=origin, origin_country=origin.apply(match))


def clean_roaster_location(df: pd.DataFrame) -> pd.DataFrame:
    """Split roaster_location into a country and, for the US, a state.

    The site writes locations most-specific-first ("Portland, Oregon"), so the
    last comma-separated part is the region. A US address names the state
    there rather than the country, which is why states are checked first.

    The source text carries several irregularities: trailing periods
    ("Montana."), the site's apostrophe spelling of Hawai'i, a missing comma
    ("Scottsdale Arizona"), and common names pycountry does not carry.
    """
    states = _us_states()

    def split(text: object) -> tuple[str, str]:
        if pd.isna(text) or not str(text).strip():
            return "", ""
        region = re.sub(
            r"\s+", " ", unidecode(str(text).split(",")[-1]).lower().replace("'", "")
        ).strip(" .")

        if region in states:
            return "united states", region
        for state in states:
            if region.endswith(" " + state):
                return "united states", state
        if region.startswith("big island of hawaii") or region == "hawaii":
            return "united states", "hawaii"
        return COUNTRY_ALIASES.get(region, region), ""

    parts = df["roaster_location"].apply(split)
    return df.assign(
        roaster_country=[country for country, _ in parts],
        roaster_us_state=[state for _, state in parts],
    )


def apply_roaster_crosswalk(df: pd.DataFrame, crosswalk: pd.DataFrame) -> pd.DataFrame:
    """Add the canonical roaster name beside the raw one.

    The raw spelling is kept: the cleaned layer adds to what was scraped rather
    than overwriting it, so an incorrect merge remains traceable to its source.
    An unresolved name falls back to its own spelling.
    """
    mapping = dict(zip(crosswalk["raw_name"], crosswalk["canonical_name"], strict=True))
    return df.assign(
        roaster_canonical=lambda d: d["roaster"].map(mapping).fillna(d["roaster"])
    )


# ==========================================================================
# The layer
# ==========================================================================


def clean_reviews(
    raw: pd.DataFrame,
    *,
    exchange_rates: pd.DataFrame | None = None,
    cpi: pd.DataFrame | None = None,
    crosswalk: pd.DataFrame | None = None,
    baseline_date: str = DEFAULT_BASELINE_DATE,
    max_agtron: int = DEFAULT_MAX_AGTRON,
) -> pd.DataFrame:
    """Raw scraped reviews in, cleaned layer out.

    The field-level work needs nothing but the raw scrape. Putting prices in
    comparable money needs exchange rates and CPI, which :mod:`coffee.enrich`
    applies; pass both and the result carries ``price_usd``, ``price_usd_adj``
    and ``price_usd_adj_per_lb`` as well.

    Omitting them returns the field-level layer alone, which is what makes this
    runnable before any reference data has been fetched.
    """
    cleaned = (
        raw.pipe(check_raw_schema)
        .pipe(normalise_types, max_agtron=max_agtron)
        .pipe(split_price_and_quantity)
        .pipe(convert_to_lbs)
        .pipe(clean_currency)
        .pipe(clean_origin)
        .pipe(clean_roaster_location)
    )
    if crosswalk is not None:
        cleaned = cleaned.pipe(apply_roaster_crosswalk, crosswalk)
    if exchange_rates is not None and cpi is not None:
        cleaned = enrich_reviews(
            cleaned,
            exchange_rates=exchange_rates,
            cpi=cpi,
            baseline_date=baseline_date,
        )
    return cleaned
