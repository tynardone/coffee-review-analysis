"""What a resolution run tells you about itself.

These lines are the only view onto whether a run did something reasonable, so
the cases worth pinning are the ones where a misleading report would send you
looking in the wrong place.
"""

import pandas as pd
import pytest

from coffee.roasters import Decision, Verdict, format_resolution_report
from coffee.roasters.report import format_violations


def crosswalk(rows):
    """Build a crosswalk frame from (raw_name, canonical_name, chain_risk)."""
    return pd.DataFrame(
        [
            {
                "raw_name": raw,
                "canonical_name": canonical,
                "cluster_id": i,
                "chain_risk": risk,
            }
            for i, (raw, canonical, risk) in enumerate(rows)
        ]
    )


@pytest.fixture
def paths(tmp_path):
    return {
        "decisions_path": tmp_path / "roaster_decisions.csv",
        "review_path": tmp_path / "roaster_review_queue.csv",
    }


def test_the_merge_count_is_spellings_minus_roasters(paths):
    cw = crosswalk(
        [
            ("Onyx", "Onyx", False),
            ("Onyx Coffee", "Onyx", False),
            ("Blue", "Blue", False),
        ]
    )
    report = format_resolution_report(cw, pd.DataFrame(), [], **paths)
    assert "3 distinct spellings -> 2 roasters (1 merged)" in report


def test_an_absent_decisions_file_reads_differently_from_an_empty_one(paths):
    """They call for different action, so they must not report the same."""
    cw = crosswalk([("A", "A", False)])

    absent = format_resolution_report(cw, pd.DataFrame(), [], **paths)
    assert "no decisions file yet" in absent

    paths["decisions_path"].write_text("name_a,name_b,verdict\n")
    empty = format_resolution_report(cw, pd.DataFrame(), [], **paths)
    assert "0 decisions" in empty and "nothing adjudicated yet" in empty


def test_applied_decisions_are_counted(paths):
    cw = crosswalk([("A", "A", False)])
    decisions = [Decision("A", "B", Verdict.MERGE), Decision("C", "D", Verdict.SPLIT)]
    report = format_resolution_report(cw, pd.DataFrame(), decisions, **paths)
    assert "2 decisions applied" in report


def test_chain_risk_is_called_out_only_when_present(paths):
    clean = crosswalk([("A", "A", False)])
    assert "INSPECT THESE" not in format_resolution_report(
        clean, pd.DataFrame(), [], **paths
    )

    risky = crosswalk([("A", "A", True), ("B", "A", True)])
    report = format_resolution_report(risky, pd.DataFrame(), [], **paths)
    assert "2 rows in chain-risk clusters" in report
    assert "INSPECT THESE" in report


def test_the_queue_size_is_reported(paths):
    cw = crosswalk([("A", "A", False)])
    review = pd.DataFrame({"name_a": ["A", "B"], "name_b": ["C", "D"]})
    assert "2 pairs queued for review" in format_resolution_report(
        cw, review, [], **paths
    )


# --------------------------------------------------------------------------
# Violated splits
# --------------------------------------------------------------------------


def test_no_violations_produces_nothing_to_print():
    cw = crosswalk([("A", "A", False)]).assign(violates_decision=False)
    assert format_violations(cw) == ""


def test_a_crosswalk_without_the_column_is_not_an_error():
    """Resolving with no decisions at all leaves the column off entirely."""
    assert format_violations(crosswalk([("A", "A", False)])) == ""


def test_a_violated_split_names_the_whole_cluster():
    cw = pd.DataFrame(
        {
            "raw_name": ["RND", "Red Rooster", "RND & Red Rooster"],
            "canonical_name": ["RND & Red Rooster"] * 3,
            "cluster_id": [0, 0, 0],
            "chain_risk": [True] * 3,
            "violates_decision": [True] * 3,
        }
    )
    out = format_violations(cw)
    assert "1 cluster(s) VIOLATE a split decision" in out
    assert "RND  |  RND & Red Rooster  |  Red Rooster" in out
    assert "bridging name" in out
