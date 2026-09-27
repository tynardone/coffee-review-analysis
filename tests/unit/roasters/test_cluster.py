"""Clustering end to end, and the two failure modes it reports.

Single-linkage can fuse A and C through B, and can rejoin a split pair through
a third name that resembles both. Neither is prevented; both are flagged.
"""

from coffee.roasters import Decision, Verdict, resolve

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
# Split decisions defeated transitively
# --------------------------------------------------------------------------

COLLAB = ["RND", "Red Rooster Coffee Roaster", "RND & Red Rooster Coffee Roaster"]


def test_a_split_can_be_defeated_by_a_bridging_name():
    """Blocking a union is not the same as keeping two names apart.

    A collaboration name is a superset of both partners' keys, so single
    linkage rejoins them through it. This is accepted behaviour — the point of
    the next test is that it must not be SILENT.
    """
    decisions = [Decision(COLLAB[0], COLLAB[1], Verdict.SPLIT)]
    crosswalk, _ = resolve(COLLAB, decisions=decisions)
    canonical = dict(zip(crosswalk.raw_name, crosswalk.canonical_name, strict=True))
    assert canonical[COLLAB[0]] == canonical[COLLAB[1]]  # still together


def test_a_defeated_split_is_reported():
    """THE ALARM. A decision accepted and then ignored is worse than one
    refused outright, so the crosswalk has to say so."""
    decisions = [Decision(COLLAB[0], COLLAB[1], Verdict.SPLIT)]
    crosswalk, _ = resolve(COLLAB, decisions=decisions)
    assert crosswalk.violates_decision.any()
    flagged = set(crosswalk[crosswalk.violates_decision].raw_name)
    assert {COLLAB[0], COLLAB[1]} <= flagged


def test_splitting_the_bridge_too_resolves_the_violation():
    """The documented fix: split against the name doing the bridging."""
    decisions = [
        Decision(COLLAB[0], COLLAB[1], Verdict.SPLIT),
        Decision(COLLAB[0], COLLAB[2], Verdict.SPLIT),
    ]
    crosswalk, _ = resolve(COLLAB, decisions=decisions)
    canonical = dict(zip(crosswalk.raw_name, crosswalk.canonical_name, strict=True))
    assert canonical[COLLAB[0]] != canonical[COLLAB[1]]
    assert not crosswalk.violates_decision.any()


def test_honoured_splits_are_not_flagged():
    decisions = [Decision("Fellow Coffee", "Mellow Coffee", Verdict.SPLIT)]
    crosswalk, _ = resolve(["Fellow Coffee", "Mellow Coffee"], decisions=decisions)
    assert not crosswalk.violates_decision.any()


def test_violates_decision_is_present_without_any_decisions():
    """The column must exist unconditionally, or downstream reads break."""
    crosswalk, _ = resolve(["Onyx Coffee", "Onyx Coffee Lab"])
    assert "violates_decision" in crosswalk.columns
    assert not crosswalk.violates_decision.any()
