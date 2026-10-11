"""Tests for reading the site's free-text prices.

Every case below is a shape the site has really used. A misread quantity does
not raise; it quietly puts a price per pound off by a factor of ten.
"""

import pytest

from coffee.price_text import PRICE_FORMATS, ParsedPrice, parse_price_text


@pytest.mark.parametrize(
    "text, value, currency, qty_value, qty_unit",
    [
        ("$19.00/16 ounces", 19.0, "$", 16.0, "ounces"),
        ("NT $500/227 grams", 500.0, "NT $", 227.0, "grams"),
        ("$45.00/1 kg", 45.0, "$", 1.0, "kilograms"),
        ("$1,200.00/12 ounces", 1200.0, "$", 12.0, "ounces"),
        ("$9.00 / 8 oz.", 9.0, "$", 8.0, "ounces"),
        ("CAD $10.00/250 grams (8.8 oz.)", 10.0, "CAD $", 250.0, "grams"),
        ("$35,000 LAK/250 grams", 35000.0, "$ LAK", 250.0, "grams"),
        ("€29.95/1 kilo (35.3 ounces)", 29.95, "€", 1.0, "kilograms"),
        ("$64.95/ 5 pounds", 64.95, "$", 5.0, "pounds"),
        ("$21.34/12ounces", 21.34, "$", 12.0, "ounces"),
        ("Price: $23.25/12 ounces", 23.25, "$", 12.0, "ounces"),
    ],
)
def test_weight_prices_are_read(text, value, currency, qty_value, qty_unit):
    assert parse_price_text(text) == ParsedPrice(
        value, currency, qty_value, qty_unit, "weight"
    )


def test_a_gram_figure_keeps_every_digit():
    """The old parser read "200g" as 20 grams, a per-pound price 10x too high."""
    assert parse_price_text("NT $520/200g").quantity_value == 200.0
    assert parse_price_text("HK $108/200g").quantity_value == 200.0


@pytest.mark.parametrize(
    "text, qty_value, qty_unit",
    [
        ("$47.30/kg.", 1.0, "kilograms"),
        ("$6.60/pound", 1.0, "pounds"),
        ("NT $1,000/kilogram", 1.0, "kilograms"),
    ],
)
def test_a_bare_unit_means_one_of_it(text, qty_value, qty_unit):
    parsed = parse_price_text(text)
    assert (parsed.quantity_value, parsed.quantity_unit) == (qty_value, qty_unit)


@pytest.mark.parametrize(
    "text, qty_value",
    [
        ("£6.50 for 250g", 250.0),  # no slash
        ("$29.00//12 ounces", 12.0),  # doubled slash
        ("$33.00/8 ouces", 8.0),  # typo
        ("$10.75/12 ouncues", 12.0),  # typo
        ("$15.99/12 onces", 12.0),  # typo
        ("$19..75/12 ounces", 12.0),  # doubled decimal point
        ("$20.00/.8 ounces", 8.0),  # stray period after the slash
        ("$39.95/8 ounces*", 8.0),
        ("$19.75/ 16 ounces.", 16.0),
    ],
)
def test_typos_in_the_quantity_are_read_through(text, qty_value):
    parsed = parse_price_text(text)
    assert parsed.format == "weight"
    assert parsed.quantity_value == qty_value


@pytest.mark.parametrize(
    "text, value, qty_value",
    [
        ("$16.00/12 ounces; $50.00/5 pounds", 16.0, 12.0),
        ("$13.99/12 ounces ($79.00/5 pounds)", 13.99, 12.0),
        # An aside never closed, carrying a second slash of its own.
        ("$33.92/10 ounces (sold as a set of 3/$101.75", 33.92, 10.0),
        ("$19.50/12 ounces online; $12.99/ounces in stores", 19.5, 12.0),
    ],
)
def test_only_the_first_offer_is_read(text, value, qty_value):
    parsed = parse_price_text(text)
    assert (parsed.value, parsed.quantity_value) == (value, qty_value)


@pytest.mark.parametrize(
    "text, price_format",
    [
        ("$30.00/10 capsules", "single_serve"),
        ("$9.99/12 K-Cups", "single_serve"),
        ("$14.99/14 Single-Serve Pods", "single_serve"),
        ("NT $500/5 single-serve brew bags", "single_serve"),
        ("$11.99/16 Vue-Packs", "single_serve"),
        ("$10.49/12 T Discs", "single_serve"),
        ("$6.50/10 capsultes", "single_serve"),
        ("$4.79/8 ounce can", "ready_to_drink"),
        ("$35.00/six 8-ounce cans", "ready_to_drink"),
        ("NT $200/150 ml. bottle", "ready_to_drink"),
        ("$13.33/32 ounce-carton", "ready_to_drink"),
        ("$5.65/16 oz. liquid concentrate", "ready_to_drink"),
        ("$9.99/12 sticks", "instant"),
        ("$22.00/eight 5-gram tubes", "instant"),
        ("$8.27/8 ounces instant", "instant"),
        ("$14.99/seven single-serve pouches", "instant"),
        ("$3.00/sachet (plus one donated)", "instant"),
        ("$32.00/boxed set (125 grams of this coffee)", "other"),
        ("$15.00", "unspecified"),
    ],
)
def test_prices_not_by_weight_keep_their_amount_but_no_quantity(text, price_format):
    """A capsule's price is still a price; it just has no price per pound."""
    parsed = parse_price_text(text)
    assert parsed.format == price_format
    assert parsed.value is not None
    assert parsed.quantity_value is None


@pytest.mark.parametrize("text", ["$30.00/150-gram tin", "£100/150-gram packet"])
def test_a_single_container_sold_by_weight_is_a_weight_price(text):
    """One tin or packet of beans, unlike "six 5-gram packets" of instant."""
    assert parse_price_text(text).format == "weight"


def test_a_word_ending_in_g_is_not_a_gram():
    assert parse_price_text("$12.00/big bag").format == "other"


@pytest.mark.parametrize(
    "text", [None, "Not Available", "See review note", "Not disclosed.", "NA"]
)
def test_text_without_a_number_is_not_a_price(text):
    assert parse_price_text(text) == ParsedPrice(None, None, None, None, None)


@pytest.mark.parametrize(
    "text, currency",
    [
        ("110 CN¥/500 grams", "CN¥"),
        ("Kč 595/500 grams", "Kč"),
        ("AU$25 /250 grams", "AU$"),
        ("$NT$1000/200g", "$NT$"),
        ("$450 NTD/100 grams", "$ NTD"),
    ],
)
def test_the_currency_is_the_text_around_the_amount(text, currency):
    """Mapped to an ISO code later, by coffee.clean.clean_currency."""
    assert parse_price_text(text).currency == currency


def test_every_format_is_documented():
    assert set(PRICE_FORMATS) == {
        "weight",
        "single_serve",
        "ready_to_drink",
        "instant",
        "other",
        "unspecified",
    }
