"""Adjudicated pairs: the only state here that cannot be regenerated.

This is the module that touches disk. The crosswalk and the review queue are
derived on every run; the decisions file is hand-edited and committed, and
keeping it separate is what lets manual effort accumulate across runs.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path

import pandas as pd

__all__ = [
    "DECISION_COLUMNS",
    "REVIEW_COLUMNS",
    "VERDICT_SYNONYMS",
    "Decision",
    "Verdict",
    "load_decisions",
    "parse_verdict",
    "promote_reviewed",
    "save_decisions",
    "unpromoted_verdicts",
]


# The crosswalk is derived and regenerable; adjudicated pairs are not. Keeping
# them in a separate file is what lets manual effort accumulate: every run
# re-derives the clusters but never re-asks an answered question.


class Verdict(StrEnum):
    MERGE = "merge"
    SPLIT = "split"


# What counts as an answer in the queue's `verdict` column. The column is
# filled in by hand, often in a spreadsheet, where "y" and "n" are the natural
# responses to "are these the same company?". Accepting only the two canonical
# spellings would discard those rows.
VERDICT_SYNONYMS: dict[str, Verdict] = {
    "merge": Verdict.MERGE,
    "m": Verdict.MERGE,
    "yes": Verdict.MERGE,
    "y": Verdict.MERGE,
    "true": Verdict.MERGE,
    "1": Verdict.MERGE,
    "same": Verdict.MERGE,
    "split": Verdict.SPLIT,
    "s": Verdict.SPLIT,
    "no": Verdict.SPLIT,
    "n": Verdict.SPLIT,
    "false": Verdict.SPLIT,
    "0": Verdict.SPLIT,
    "different": Verdict.SPLIT,
    "diff": Verdict.SPLIT,
}


def parse_verdict(value: object) -> Verdict | None:
    """Read one cell of the `verdict` column. None means "not answered"."""
    text = str(value).strip().lower()
    if not text or text in {"nan", "none"}:
        return None
    return VERDICT_SYNONYMS.get(text)


def unpromoted_verdicts(review_path: Path) -> int:
    """How many answered rows in this queue are not yet in the decisions file.

    The caller uses this to refuse to regenerate a queue that still holds
    unsaved work.
    """
    if not review_path.exists():
        return 0
    frame = pd.read_csv(review_path).fillna("")
    if "verdict" not in frame.columns:
        return 0
    return sum(1 for value in frame["verdict"] if str(value).strip())


@dataclass(frozen=True)
class Decision:
    """One adjudicated pair.

    `decided_by` records the source of the verdict, so that decisions from one
    source -- a particular person, or a model run -- can be revisited as a
    group without disturbing the rest.
    """

    name_a: str
    name_b: str
    verdict: Verdict
    decided_by: str = ""
    decided_on: str = ""
    note: str = ""

    @property
    def key(self) -> tuple[str, str]:
        """Order-independent identity, so (a, b) and (b, a) are one decision."""
        return tuple(sorted((self.name_a, self.name_b)))  # type: ignore[return-value]


DECISION_COLUMNS = ["name_a", "name_b", "verdict", "decided_by", "decided_on", "note"]

# Columns of the review queue. `verdict` is the one to fill in; the location
# columns carry the evidence so that adjudicating a pair needs no second
# lookup.
REVIEW_COLUMNS = [
    "name_a",
    "name_b",
    "core_a",
    "core_b",
    "score",
    "location_a",
    "location_b",
    "location_evidence",
    "verdict",
]


def load_decisions(path: Path) -> list[Decision]:
    """Read adjudicated pairs; a missing file simply means none yet."""
    if not path.exists():
        return []
    frame = pd.read_csv(path).fillna("")
    missing = set(DECISION_COLUMNS[:3]) - set(frame.columns)
    if missing:
        raise ValueError(f"{path} is missing column(s): {sorted(missing)}")
    return [
        Decision(
            name_a=row["name_a"],
            name_b=row["name_b"],
            verdict=Verdict(row["verdict"]),
            decided_by=row.get("decided_by", ""),
            decided_on=row.get("decided_on", ""),
            note=row.get("note", ""),
        )
        for _, row in frame.iterrows()
    ]


def promote_reviewed(
    review_path: Path, decisions_path: Path, decided_by: str = "manual"
) -> int:
    """Move answered rows out of the review queue and into the decisions file.

    Answers only persist once promoted: the next run re-derives the queue from
    scratch, so a verdict left in the queue is asked again. Promoting is what
    limits each question to being asked once.

    Blank rows are left alone, so a partially filled queue is valid. A value
    that cannot be parsed raises rather than being skipped, since dropping
    answers silently is indistinguishable from the tool not working.

    Returns the number of new decisions recorded.
    """
    if not review_path.exists():
        return 0
    queue = pd.read_csv(review_path).fillna("")
    if "verdict" not in queue.columns:
        raise ValueError(f"{review_path} has no 'verdict' column to read.")

    existing = {d.key: d for d in load_decisions(decisions_path)}
    today = datetime.now().strftime("%Y-%m-%d")
    added = 0
    unreadable: list[str] = []
    for _, row in queue.iterrows():
        verdict = parse_verdict(row["verdict"])
        if verdict is None:
            if str(row["verdict"]).strip():
                unreadable.append(
                    f"{row['name_a']} ~ {row['name_b']}: {row['verdict']!r}"
                )
            continue
        decision = Decision(
            name_a=row["name_a"],
            name_b=row["name_b"],
            verdict=verdict,
            decided_by=decided_by,
            decided_on=today,
            note=str(row.get("note", "")),
        )
        # An existing verdict is never overwritten here. Changing one means
        # editing the decisions file directly, where it shows up in git.
        if decision.key not in existing:
            existing[decision.key] = decision
            added += 1

    if unreadable:
        readable = ", ".join(sorted(set(VERDICT_SYNONYMS)))
        raise ValueError(
            f"{len(unreadable)} row(s) in {review_path} have a verdict that "
            f"cannot be read, so nothing was recorded. Fix them and re-run.\n  "
            + "\n  ".join(unreadable[:10])
            + f"\nAccepted values: {readable}"
        )

    save_decisions(existing.values(), decisions_path)
    return added


def save_decisions(decisions: Iterable[Decision], path: Path) -> None:
    """Write decisions back out, sorted so the file diffs cleanly in git."""
    rows = sorted(
        ({c: getattr(d, c) for c in DECISION_COLUMNS} for d in decisions),
        key=lambda r: (r["name_a"], r["name_b"]),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=DECISION_COLUMNS).to_csv(path, index=False)
