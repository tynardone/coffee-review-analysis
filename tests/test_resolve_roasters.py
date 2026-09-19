"""Entity-resolution tests, centered on the subset-match hazard.

The governing asymmetry in resolve_roasters.py is that a FALSE MERGE is silent
and a false split is obvious, so these tests weight precision accordingly: the
merges asserted below are ones that must happen, and the separations are ones
whose absence corrupted the real crosswalk.
"""

import pytest
from resolve_roasters import (
    core_key,
    fingerprint,
    resolve,
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
    assert review.iloc[0]["merge"] == ""


def test_chain_risk_flags_transitively_assembled_clusters():
    """Single-linkage chaining is accepted only because it is detectable."""
    names = ["Aroma Roast Coffees", "Aroma Roast", "Roast Coffees Aroma"]
    crosswalk, _ = resolve(names)
    assert "chain_risk" in crosswalk.columns
    assert crosswalk.chain_risk.dtype == bool
