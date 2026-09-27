"""The location signal, and the asymmetry it is used with.

A region conflict vetoes a merge; a matching location only surfaces a pair.
Merging on a matching location was tried and is unsafe -- the score does not
separate a true match at 82.1 from unrelated companies sharing a city at 82.9.
"""

import pytest

from coffee.roasters import (
    LocationEvidence,
    compare_locations,
    core_key,
    normalize_location,
    resolve,
)

# --------------------------------------------------------------------------
# Location — the second signal
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("London, Ontario, Canada", ("london", "canada")),
        ("Portland, Oregon", ("portland", "oregon")),
        ("El Salvador", (None, "el salvador")),
        ("  Taipei ,  Taiwan  ", ("taipei", "taiwan")),
        ("Montréal, Québec", ("montreal", "quebec")),
    ],
)
def test_normalize_location(raw, expected):
    assert normalize_location(raw) == expected


@pytest.mark.parametrize(
    "a, b, expected",
    [
        (["Portland, Oregon"], ["Portland, Oregon"], LocationEvidence.SAME),
        (["Portland, Oregon"], ["Taipei, Taiwan"], LocationEvidence.CONFLICT),
        (["Seattle, Washington"], ["Tacoma, Washington"], LocationEvidence.NEUTRAL),
        (None, ["Taipei, Taiwan"], LocationEvidence.UNKNOWN),
        ([], ["Taipei, Taiwan"], LocationEvidence.UNKNOWN),
    ],
)
def test_compare_locations(a, b, expected):
    assert compare_locations(a, b) is expected


def test_a_relocated_roaster_is_not_a_conflict():
    """Names carry a SET of locations because roasters move.

    155 of 1,591 roasters in the real data appear at more than one address.
    Any overlap has to withhold the veto, or every relocation reads as two
    different companies.
    """
    moved = ["Portland, Oregon", "Taipei, Taiwan"]
    assert compare_locations(moved, ["Taipei, Taiwan"]) is LocationEvidence.SAME


def test_region_conflict_blocks_a_merge_the_name_score_would_allow():
    """THE PRECISION WIN.

    'Heart Coffee Roasters' and 'Heat Coffee' score 88.9 on name alone. One is
    in Portland, the other in Taipei.
    """
    names = ["Heart Coffee Roasters", "Heat Coffee"]
    places = {
        "Heart Coffee Roasters": {"Portland, Oregon"},
        "Heat Coffee": {"Taipei, Taiwan"},
    }
    crosswalk, review = resolve(names, locations=places, auto_threshold=80)
    canonical = dict(zip(crosswalk.raw_name, crosswalk.canonical_name, strict=True))
    assert canonical["Heart Coffee Roasters"] != canonical["Heat Coffee"]
    assert len(review) == 0  # resolved, so not a question for a human


def test_location_veto_also_applies_to_exact_key_collisions():
    """Two names can stopword down to the SAME key and still be different.

    'Direct Coffee' and 'Coffee Bean Direct' both reduce to 'direct'. No string
    evidence can separate them, which is why the veto has to reach Stage A.
    """
    names = ["Direct Coffee", "Coffee Bean Direct"]
    assert core_key(names[0]) == core_key(names[1])
    places = {
        "Direct Coffee": {"Seattle, Washington"},
        "Coffee Bean Direct": {"Easton, Pennsylvania"},
    }
    crosswalk, _ = resolve(names, locations=places)
    assert crosswalk.canonical_name.nunique() == 2


def test_matching_location_surfaces_but_never_merges():
    """The two directions are deliberately asymmetric.

    Merging on a lowered bar is unsafe: 'Great Value (Walmart)'/'Great Value
    (Wal-Mart)' scores 82.1 and is right, while 'Tehmag Foods'/'Wei Chuan
    Foods' — unrelated companies in one city — scores 82.9 and is wrong. No
    threshold separates them, so a location match may only ask a question.
    """
    names = ["Tehmag Foods Corporation", "Wei Chuan Foods Corporation"]
    places = dict.fromkeys(names, {"Taipei, Taiwan"})
    crosswalk, review = resolve(names, locations=places)
    assert crosswalk.canonical_name.nunique() == 2
    assert len(review) == 1
    assert review.iloc[0]["location_evidence"] == "same"


def test_location_review_threshold_surfaces_pairs_below_the_floor():
    """Recall the name score alone cannot reach.

    These score 76.2 — under the 82 floor, so normally invisible. Sharing an
    address is the only reason to look at them at all, and looking is all this
    does: the pair is queued, never merged.
    """
    names = ["White Rhino Coffee", "White Rock Coffee"]
    places = dict.fromkeys(names, {"Dallas, Texas"})

    assert len(resolve(names, locations=places)[1]) == 0

    crosswalk, surfaced = resolve(names, locations=places, location_review_threshold=70)
    assert len(surfaced) == 1
    assert crosswalk.canonical_name.nunique() == 2  # surfaced, not merged
