"""Read the site's free-text price into an amount, a currency and a quantity.

The site writes prices by hand, in about two hundred shapes: ``$19.00/16
ounces``, ``NT $500/227 grams``, ``£6.50 for 250g``, ``$45.00/1 kg``,
``$9.99/12 sticks``. :func:`parse_price_text` reads one string, and
:func:`coffee.clean.split_price_and_quantity` applies it to every review.

Every price is kept, whatever it buys. :data:`PRICE_FORMATS` says what that is,
and only a ``weight`` price has a quantity that converts to pounds:

- ``weight``: coffee sold by weight, the case a price per pound is meant for
- ``single_serve``: K-Cups, capsules, pods, brew bags
- ``ready_to_drink``: cans, bottles, cartons, concentrate
- ``instant``: instant coffee, sticks, tubes, sachets, packets
- ``other``: something else, such as a boxed set
- ``unspecified``: a price with no quantity at all
"""

import re
from dataclasses import dataclass
from typing import Final, Literal

__all__ = ["PRICE_FORMATS", "WEIGHT_UNITS", "ParsedPrice", "parse_price_text"]

PriceFormat = Literal[
    "weight", "single_serve", "ready_to_drink", "instant", "other", "unspecified"
]

PRICE_FORMATS: Final[tuple[PriceFormat, ...]] = (
    "weight",
    "single_serve",
    "ready_to_drink",
    "instant",
    "other",
    "unspecified",
)

# The spellings of each unit the site uses, typos included, mapped to the unit
# names that coffee.clean.US_PRICE_UNITS converts to pounds.
WEIGHT_UNITS: Final[dict[str, str]] = {
    "kilograms": "kilograms",
    "kilogram": "kilograms",
    "kilos": "kilograms",
    "kilo": "kilograms",
    "kg": "kilograms",
    "grams": "grams",
    "gram": "grams",
    "g": "grams",
    "ounces": "ounces",
    "ounce": "ounces",
    "ouncues": "ounces",
    "onces": "ounces",
    "ouces": "ounces",
    "oz": "ounces",
    "pounds": "pounds",
    "pound": "pounds",
    "lbs": "pounds",
    "lb": "pounds",
}

# Checked in this order, and the first match wins. Containers come before the
# instant words because "single-serve" appears in both "single-serve brew
# bags" and "single-serve packets", and the container is what decides.
_FORMAT_PATTERNS: Final[tuple[tuple[PriceFormat, re.Pattern[str]], ...]] = (
    (
        "ready_to_drink",
        re.compile(
            r"\b(cans?|bottles?|flasks?|cartons?|ml|fluid|liquid|concentrate)\b"
        ),
    ),
    (
        "single_serve",
        re.compile(
            r"k-?cups?|capsul\w*|\bpods?\b|brew bags?|vue-?packs?|\bdiscs?\b"
            r"|single-serve cups?|realcup"
        ),
    ),
    (
        "instant",
        re.compile(r"\binstant\b|\bsticks?\b|\btubes?\b|\bsachets?\b|packets|pouches"),
    ),
)

# A number as the site writes one: "1,200.00", "19.00", ".8", "35,000".
_NUMBER: Final = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d*\.\d+|\d+"
# The unit must not follow a letter, so the "g" ending "big" is not a gram.
_WEIGHT: Final = re.compile(
    rf"(?P<value>{_NUMBER})?\s*-?\s*(?<![a-z])(?P<unit>"
    + "|".join(sorted(map(re.escape, WEIGHT_UNITS), key=len, reverse=True))
    + r")\b"
)


@dataclass(frozen=True)
class ParsedPrice:
    """One price string, read into parts.

    ``currency`` is the text around the amount as the site wrote it ("NT $",
    "CAD $", "£"); :func:`coffee.clean.clean_currency` maps it to an ISO code.
    The quantity is set only for a ``weight`` price.
    """

    value: float | None
    currency: str | None
    quantity_value: float | None
    quantity_unit: str | None
    format: PriceFormat | None


_NO_PRICE: Final = ParsedPrice(None, None, None, None, None)


def _first_offer(text: str) -> str:
    """The first price the text names, without asides.

    Some entries list several offers ("$16.00/12 ounces; $50.00/5 pounds")
    or add a note in parentheses ("$13.99/12 ounces ($79.00/5 pounds)"),
    sometimes never closed ("(sold as a set of 3/$101.75"). The first offer is
    the one the review describes.
    """
    text = text.split(";", 1)[0]
    text = re.sub(r"\([^)]*\)", " ", text)  # closed asides
    text = text.split("(", 1)[0]  # an aside left open runs to the end
    return re.sub(r"\s+", " ", text).strip()


def _container_format(quantity: str) -> PriceFormat | None:
    """The format a container word names, or None if none does."""
    for name, pattern in _FORMAT_PATTERNS:
        if pattern.search(quantity):
            return name
    return None


def parse_price_text(text: str | None) -> ParsedPrice:
    """Read one price string; every part is None if it names no price.

    A text with no digits, such as "Not Available" or "See review note", is
    not a price. A price with no quantity ("$15.00") keeps its amount and is
    ``unspecified``. A bare unit ("$12.00/pound", "$47.30/kg") means one of it.
    """
    if text is None or not re.search(r"\d", text):
        return _NO_PRICE

    offer = _first_offer(text.replace("..", "."))
    offer = re.sub(r"^price:\s*", "", offer, flags=re.IGNORECASE)
    if "/" in offer:
        price_part, quantity = offer.split("/", 1)
        quantity = quantity.lstrip("/ ")  # "$29.00//12 ounces"
    elif " for " in offer:
        price_part, quantity = offer.split(" for ", 1)  # "£6.50 for 250g"
    else:
        price_part, quantity = offer, ""

    amount = re.search(_NUMBER, price_part)
    if amount is None:
        return _NO_PRICE
    value = float(amount.group().replace(",", ""))
    currency = (price_part[: amount.start()] + price_part[amount.end() :]).strip()

    quantity = quantity.lower().strip().rstrip(".*").strip()
    # A period straight after the slash is a stray keystroke: "$20.00/.8
    # ounces" sells 8 ounces, not a fraction of one. The site writes a real
    # fraction with its leading zero.
    quantity = re.sub(r"^\.(?=\d)", "", quantity)
    if not quantity:
        return ParsedPrice(value, currency, None, None, "unspecified")

    container = _container_format(quantity)
    if container is not None:
        return ParsedPrice(value, currency, None, None, container)

    weight = _WEIGHT.search(quantity)
    if weight is None:
        return ParsedPrice(value, currency, None, None, "other")
    amount_text = weight.group("value")
    return ParsedPrice(
        value,
        currency,
        float(amount_text.replace(",", "")) if amount_text else 1.0,
        WEIGHT_UNITS[weight.group("unit")],
        "weight",
    )
