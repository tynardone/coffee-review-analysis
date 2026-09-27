"""Normalization and the subset guard.

These two decide what "the same name" means before any threshold is involved,
so a bug here moves every downstream result.
"""

import pytest

from coffee.roasters import (
    core_key,
    fingerprint,
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
