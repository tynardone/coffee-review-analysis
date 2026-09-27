"""Adjudicated pairs: recording them, reading them back, applying them.

The queue is filled in by hand, so the cases that matter are the ones where a
session of answers could be silently discarded.
"""

import pandas as pd
import pytest

from coffee.roasters import (
    Decision,
    Verdict,
    load_decisions,
    parse_verdict,
    promote_reviewed,
    resolve,
    save_decisions,
    unpromoted_verdicts,
)

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


# --------------------------------------------------------------------------
# Reading the queue back — where a session of work gets lost
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "cell, expected",
    [
        ("merge", Verdict.MERGE),
        ("MERGE", Verdict.MERGE),
        ("  merge  ", Verdict.MERGE),
        ("y", Verdict.MERGE),
        ("yes", Verdict.MERGE),
        ("m", Verdict.MERGE),
        ("same", Verdict.MERGE),
        ("1", Verdict.MERGE),
        ("split", Verdict.SPLIT),
        ("n", Verdict.SPLIT),
        ("no", Verdict.SPLIT),
        ("different", Verdict.SPLIT),
        ("0", Verdict.SPLIT),
        ("", None),
        ("   ", None),
        ("nan", None),
    ],
)
def test_parse_verdict_accepts_what_a_person_would_type(cell, expected):
    """THE REGRESSION.

    Only the exact strings "merge"/"split" used to be accepted, and anything
    else was skipped in silence — so a queue filled in with y/n recorded
    nothing and gave no indication why.
    """
    assert parse_verdict(cell) is expected


def test_an_unreadable_verdict_is_reported_not_swallowed(tmp_path):
    review, decisions = tmp_path / "q.csv", tmp_path / "d.csv"
    pd.DataFrame(
        [
            {"name_a": "A", "name_b": "B", "verdict": "merge"},
            {"name_a": "C", "name_b": "D", "verdict": "probably?"},
        ]
    ).to_csv(review, index=False)

    with pytest.raises(ValueError, match="cannot be read"):
        promote_reviewed(review, decisions)

    # Nothing is half-recorded: fix the file and re-run, don't guess which
    # rows made it through.
    assert not decisions.exists()


def test_unpromoted_verdicts_counts_unsaved_work(tmp_path):
    review = tmp_path / "q.csv"
    assert unpromoted_verdicts(review) == 0  # missing file

    pd.DataFrame(
        [
            {"name_a": "A", "name_b": "B", "verdict": "merge"},
            {"name_a": "C", "name_b": "D", "verdict": ""},
            {"name_a": "E", "name_b": "F", "verdict": "n"},
        ]
    ).to_csv(review, index=False)
    assert unpromoted_verdicts(review) == 2


def test_synonyms_round_trip_into_canonical_decisions(tmp_path):
    """Whatever you type, the decisions file stores one canonical spelling."""
    review, decisions = tmp_path / "q.csv", tmp_path / "d.csv"
    pd.DataFrame(
        [
            {"name_a": "A", "name_b": "B", "verdict": "y"},
            {"name_a": "C", "name_b": "D", "verdict": "no"},
        ]
    ).to_csv(review, index=False)

    assert promote_reviewed(review, decisions) == 2
    stored = {d.key: d.verdict for d in load_decisions(decisions)}
    assert stored[("A", "B")] is Verdict.MERGE
    assert stored[("C", "D")] is Verdict.SPLIT
