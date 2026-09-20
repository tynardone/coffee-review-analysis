"""Entity-resolution tests, centered on the subset-match hazard.

A false merge is silent and a false split is obvious, so these tests weight
precision accordingly: the merges asserted here are ones that must happen, the
separations ones whose absence corrupted the real crosswalk.
"""

import pandas as pd
import pytest

from coffee.roaster_resolution import (
    Decision,
    LocationEvidence,
    Verdict,
    compare_locations,
    core_key,
    fingerprint,
    load_decisions,
    normalize_location,
    promote_reviewed,
    resolve,
    save_decisions,
    score,
    token_document_frequency,
    tokens,
)

# --------------------------------------------------------------------------
# Normalization
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Stumptown", "stumptown"),
        ("Stumptown Coffee", "stumptown"),
        ("Stumptown Roasters", "stumptown"),
        ("Stumptown Coffee Roasters", "stumptown"),
        ("STUMPTOWN COFFEE ROASTERS, LLC", "stumptown"),
    ],
)
def test_suffix_drift_collapses_to_one_key(raw, expected):
    """The whole cascade rests on this: variants collide exactly, for free."""
    assert core_key(raw) == expected


def test_accents_are_stripped():
    assert core_key("Caffè Artigiano") == core_key("Caffe Artigiano")


def test_apostrophe_variants_agree():
    assert core_key("Simon Hsieh's Aroma Roast") == core_key(
        "Simon Hsieh’s Aroma Roast"
    )


def test_initialisms_agree_with_their_solid_form():
    """'J.B.C. Coffee' -> ['j','b','c'] shares nothing with 'JBC' unless glued."""
    assert core_key("J.B.C. Coffee Roasters") == core_key("JBC Coffee")


def test_abbreviations_expand_before_stopwording():
    assert tokens("Dunn Bros.") == tokens("Dunn Brothers")


def test_all_stopword_name_falls_back_to_fingerprint():
    """'The Coffee Company' stopwords to nothing; it must keep some identity."""
    assert core_key("The Coffee Company") == fingerprint("The Coffee Company")
    assert core_key("The Coffee Company") != ""


# --------------------------------------------------------------------------
# The subset guard
# --------------------------------------------------------------------------


def test_subset_match_is_refused_for_a_generic_one_token_key():
    """THE REGRESSION.

    'Kona Cafe' -> 'kona', a strict subset of every 'kona <x>' key, which
    unguarded scored 100 and chained 20 unrelated roasters into one cluster.
    """
    df = {"kona": 15, "luna": 1}
    assert score("kona", "kona luna", token_df=df) < 92


def test_subset_match_is_allowed_for_a_rare_one_token_key():
    """The flip side: a distinctive short key must still merge."""
    df = {"peerless": 2, "tea": 30}
    assert score("peerless", "peerless tea", token_df=df) >= 92


def test_multi_token_subset_is_always_trusted():
    assert score("lab onyx", "coffee lab onyx", token_df={}) >= 92


def test_absent_corpus_statistic_is_the_conservative_reading():
    """No token_df must mean 'do not trust subsets', never 'trust them all'."""
    assert score("kona", "kona luna") < 92


def test_token_document_frequency_counts_distinct_keys_not_occurrences():
    df = token_document_frequency(["kona luna", "kona view", "kona kona"])
    assert df["kona"] == 3
    assert df["luna"] == 1


# --------------------------------------------------------------------------
# End-to-end clustering
# --------------------------------------------------------------------------


def test_generic_head_does_not_fuse_unrelated_roasters():
    """Reproduces the 'Dallis Bros.' cluster that swallowed every 'Brothers'."""
    names = [
        "Dallis Bros. Coffee",
        "Dunn Brothers",
        "Dunn Bros. Coffee",
        "Evans Brothers Coffee",
        "Kaladi Brothers Coffee",
        "Coffee Bros.",
    ]
    crosswalk, _ = resolve(names)
    canonical = dict(zip(crosswalk.raw_name, crosswalk.canonical_name, strict=True))

    assert canonical["Dunn Brothers"] == canonical["Dunn Bros. Coffee"]
    for a, b in [
        ("Dallis Bros. Coffee", "Dunn Brothers"),
        ("Evans Brothers Coffee", "Kaladi Brothers Coffee"),
        ("Dallis Bros. Coffee", "Evans Brothers Coffee"),
    ]:
        assert canonical[a] != canonical[b], f"{a} and {b} must not merge"


def test_real_variants_still_merge():
    names = [
        "Onyx Coffee Lab",
        "Onyx Coffee Lab LLC",
        "onyx coffee lab",
        "Counter Culture Coffee",
        "Counter Culture",
    ]
    crosswalk, _ = resolve(names)
    canonical = dict(zip(crosswalk.raw_name, crosswalk.canonical_name, strict=True))
    assert len({canonical[n] for n in names[:3]}) == 1
    assert canonical["Counter Culture Coffee"] == canonical["Counter Culture"]


def test_canonical_name_is_the_most_frequent_spelling():
    names = ["Onyx Coffee Lab"] * 5 + ["onyx coffee lab"] * 2
    crosswalk, _ = resolve(names)
    assert set(crosswalk.canonical_name) == {"Onyx Coffee Lab"}


def test_uncertain_pairs_land_in_the_review_queue_not_in_a_merge():
    """The middle band must be surfaced for judgment, not silently decided."""
    names = ["Black Oak Coffee Roasters", "Black Mountain Coffee Roasters"]
    crosswalk, review = resolve(names, auto_threshold=92, review_threshold=50)
    assert crosswalk.canonical_name.nunique() == 2
    assert len(review) == 1
    assert review.iloc[0]["verdict"] == ""


def test_chain_risk_flags_transitively_assembled_clusters():
    """Single-linkage chaining is accepted only because it is detectable."""
    names = ["Aroma Roast Coffees", "Aroma Roast", "Roast Coffees Aroma"]
    crosswalk, _ = resolve(names)
    assert "chain_risk" in crosswalk.columns
    assert crosswalk.chain_risk.dtype == bool


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


# --------------------------------------------------------------------------
# Decisions — durable human judgement
# --------------------------------------------------------------------------


def test_a_merge_decision_overrides_a_low_score():
    names = ["Boyd Coffee", "Boyds Coffee"]
    decisions = [Decision("Boyd Coffee", "Boyds Coffee", Verdict.MERGE)]
    crosswalk, review = resolve(names, decisions=decisions)
    assert crosswalk.canonical_name.nunique() == 1
    assert len(review) == 0


def test_a_split_decision_overrides_a_high_score():
    names = ["Onyx Coffee Lab", "Onyx Coffee Lab LLC"]
    assert resolve(names)[0].canonical_name.nunique() == 1
    decisions = [Decision(*names, Verdict.SPLIT)]
    assert resolve(names, decisions=decisions)[0].canonical_name.nunique() == 2


def test_decided_pairs_are_never_re_asked():
    """The property the whole decisions file exists for."""
    names = ["Fellow Coffee", "Mellow Coffee"]
    assert len(resolve(names)[1]) == 1
    decisions = [Decision("Mellow Coffee", "Fellow Coffee", Verdict.SPLIT)]
    assert len(resolve(names, decisions=decisions)[1]) == 0


def test_decision_identity_ignores_pair_order():
    assert (
        Decision("b", "a", Verdict.MERGE).key == Decision("a", "b", Verdict.SPLIT).key
    )


def test_decisions_round_trip(tmp_path):
    path = tmp_path / "decisions.csv"
    original = [
        Decision("A", "B", Verdict.MERGE, "tyler", "2026-09-19", "same shop"),
        Decision("C", "D", Verdict.SPLIT, "llm:opus-5", "2026-09-19", ""),
    ]
    save_decisions(original, path)
    assert sorted(load_decisions(path), key=lambda d: d.key) == sorted(
        original, key=lambda d: d.key
    )


def test_load_decisions_tolerates_a_missing_file(tmp_path):
    assert load_decisions(tmp_path / "nope.csv") == []


def test_promote_reviewed_closes_the_loop(tmp_path):
    review = tmp_path / "queue.csv"
    decisions = tmp_path / "decisions.csv"
    pd.DataFrame(
        [
            {"name_a": "A", "name_b": "B", "verdict": "merge"},
            {"name_a": "C", "name_b": "D", "verdict": "split"},
            {"name_a": "E", "name_b": "F", "verdict": ""},  # unanswered
            {"name_a": "G", "name_b": "H", "verdict": "maybe"},  # unrecognized
        ]
    ).to_csv(review, index=False)

    assert promote_reviewed(review, decisions, decided_by="tyler") == 2
    recorded = {d.key: d for d in load_decisions(decisions)}
    assert set(recorded) == {("A", "B"), ("C", "D")}
    assert recorded[("A", "B")].verdict is Verdict.MERGE
    assert recorded[("A", "B")].decided_by == "tyler"


def test_promote_reviewed_does_not_overwrite_an_existing_verdict(tmp_path):
    """Changing your mind should be an edit you can see in git, not a silent
    overwrite by a re-run."""
    review = tmp_path / "queue.csv"
    decisions = tmp_path / "decisions.csv"
    save_decisions([Decision("A", "B", Verdict.SPLIT, "tyler")], decisions)
    pd.DataFrame([{"name_a": "A", "name_b": "B", "verdict": "merge"}]).to_csv(
        review, index=False
    )

    assert promote_reviewed(review, decisions) == 0
    assert load_decisions(decisions)[0].verdict is Verdict.SPLIT
