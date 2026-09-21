"""What a resolution run says about itself.

Kept out of the command so it can be tested: the summary reports how much was
merged, how much manual work is outstanding, and whether the clustering did
anything that needs looking at. Those are judgements about the result, not
about argument parsing.
"""

from collections.abc import Sequence
from pathlib import Path

import pandas as pd

from coffee.roasters.decisions import Decision

__all__ = [
    "format_resolution_report",
    "format_violations",
]


def format_resolution_report(
    crosswalk: pd.DataFrame,
    review: pd.DataFrame,
    decisions: Sequence[Decision],
    *,
    decisions_path: Path,
    review_path: Path,
) -> str:
    """Four lines: what merged, what is applied, what is left, what to inspect.

    The order is the order they are worth reading in, ending with the chain
    risk, which is the one that can be silently wrong.
    """
    n_raw = crosswalk.raw_name.nunique()
    n_canonical = crosswalk.canonical_name.nunique()
    lines = [
        f"{n_raw} distinct spellings -> {n_canonical} roasters "
        f"({n_raw - n_canonical} merged)"
    ]

    # An absent decisions file and an empty one call for different action, so
    # they are reported differently.
    if decisions:
        lines.append(f"{len(decisions)} decisions applied from {decisions_path}")
    elif decisions_path.exists():
        lines.append(f"0 decisions in {decisions_path} (nothing adjudicated yet)")
    else:
        lines.append(f"no decisions file yet; it will be created at {decisions_path}")

    lines.append(f"{len(review)} pairs queued for review -> {review_path}")
    flagged = int(crosswalk.chain_risk.sum())
    lines.append(
        f"{flagged} rows in chain-risk clusters"
        f"{'  <-- INSPECT THESE' if crosswalk.chain_risk.any() else ''}"
    )
    return "\n".join(lines)


def format_violations(crosswalk: pd.DataFrame) -> str:
    """Splits the clustering defeated transitively, or "" if there are none.

    Reported prominently: a decision that was accepted and then not applied is
    harder to notice than one that was refused outright.
    """
    if "violates_decision" not in crosswalk or not crosswalk.violates_decision.any():
        return ""

    offenders = crosswalk[crosswalk.violates_decision]
    lines = [
        f"\n!! {offenders.cluster_id.nunique()} cluster(s) VIOLATE a split "
        "decision -- these names were kept together despite your verdict:"
    ]
    lines += [
        "    " + "  |  ".join(sorted(group.raw_name))
        for _, group in offenders.groupby("cluster_id")
    ]
    lines.append(
        "   A split can be defeated through a third name that resembles both\n"
        "   (often a collaboration, e.g. 'A & B Coffee'). Record a split "
        "against\n   that bridging name too."
    )
    return "\n".join(lines)
