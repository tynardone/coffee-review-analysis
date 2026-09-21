"""The second signal: what two roasters' locations say about them.

Asymmetric by design. A region conflict vetoes a merge; a matching location
only surfaces a pair for review.
"""

from collections.abc import Iterable
from enum import StrEnum

from coffee.roasters.normalize import strip_accents

__all__ = [
    "LocationEvidence",
    "compare_locations",
    "normalize_location",
]


# Names alone leave a wide band of uncertainty. Roaster location narrows it:
# the field is populated on nearly every review and is close to orthogonal to
# spelling, so it carries information the string score does not. On the current
# review queue it settled 41 of 50 pairs -- "Heart Coffee Roasters" (Portland,
# Oregon) against "Heat Coffee" (Taipei, Taiwan) scores 88.9 on name alone.
#
# Location is evidence rather than proof, and the two directions differ in
# strength:
#   different region  near-decisive that these are different companies
#   identical place   confirmatory, but "Bear Coffee" and "Bear Coffee
#                     Roasters" in one city could still be two businesses
# A region conflict therefore blocks a merge, while an exact match only
# surfaces the pair for review.


class LocationEvidence(StrEnum):
    """What the two names' locations say about whether they are one company."""

    SAME = "same"  # identical place: surface for review
    CONFLICT = "conflict"  # no region in common: refuse to merge
    NEUTRAL = "neutral"  # same region, different city: no opinion
    UNKNOWN = "unknown"  # at least one side has no location


def normalize_location(value: str) -> tuple[str | None, str | None]:
    """``"London, Ontario, Canada"`` -> ``("london", "canada")``.

    Returns (city, region). CoffeeReview writes locations most-specific-first,
    so the last comma-separated part is the region or country and the first is
    the city. A single-part value such as "El Salvador" is a region with no
    city.
    """
    parts = [strip_accents(part).strip().lower() for part in str(value).split(",")]
    parts = [part for part in parts if part]
    if not parts:
        return None, None
    if len(parts) == 1:
        return None, parts[0]
    return parts[0], parts[-1]


def compare_locations(
    places_a: Iterable[str] | None, places_b: Iterable[str] | None
) -> LocationEvidence:
    """Weigh two names' location sets against each other.

    Each name carries a set of locations rather than one, because a roaster can
    appear at several over the years; 155 of 1,591 in the current data do.
    Comparing sets keeps a relocation from reading as a conflict, since any
    overlap withholds the veto.
    """
    norm_a = {normalize_location(p) for p in places_a or () if p}
    norm_b = {normalize_location(p) for p in places_b or () if p}
    norm_a.discard((None, None))
    norm_b.discard((None, None))
    if not norm_a or not norm_b:
        return LocationEvidence.UNKNOWN

    if norm_a & norm_b:
        return LocationEvidence.SAME

    regions_a = {region for _, region in norm_a if region}
    regions_b = {region for _, region in norm_b if region}
    if regions_a and regions_b and not (regions_a & regions_b):
        return LocationEvidence.CONFLICT
    return LocationEvidence.NEUTRAL
