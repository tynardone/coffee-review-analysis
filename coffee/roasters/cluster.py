"""Turn scored pairs into clusters, and clusters into a crosswalk.

Single-linkage via union-find, plus the two alarms that make that affordable:
`chain_risk` for clusters assembled transitively, and `violates_decision` for a
split defeated through a bridging name.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from functools import partial

import pandas as pd
from rapidfuzz import process

from coffee.roasters.decisions import REVIEW_COLUMNS, Decision, Verdict
from coffee.roasters.location import LocationEvidence, compare_locations
from coffee.roasters.normalize import core_key
from coffee.roasters.similarity import score, token_document_frequency

__all__ = [
    "DSU",
    "resolve",
]


class DSU:
    """Turns pairs into groups: link every pair above threshold, then read off
    the connected components.

    This is single-linkage clustering, so merges are transitive: A~B and B~C
    puts A and C together even if they would score 40 against each other. The
    chaining is detectable -- resolve() reports each cluster's worst internal
    score as `chain_risk`. See "Known limits" in docs/roaster-resolution.md for
    when complete linkage is the better trade.
    """

    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]  # path compression
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


# ==========================================================================
# 6. RESOLUTION
# ==========================================================================


def resolve(
    raw_names: list[str],
    locations: Mapping[str, Iterable[str]] | None = None,
    decisions: Iterable[Decision] | None = None,
    auto_threshold: int = 92,
    review_threshold: int = 82,
    location_review_threshold: int | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cluster raw names; return (crosswalk, review_queue).

    Two thresholds rather than one, because there is a middle band in which the
    strings do not contain the answer: "Black Oak Coffee Roasters" against
    "Black & White Coffee Roasters" scores 71, and only knowledge outside the
    strings settles that they are different companies.

        score >= auto_threshold      merge automatically
        review..auto                 honest uncertainty -> review queue
        score <  review_threshold    leave alone

    The defaults of 92 and 82 are tuned on a small sample; see
    docs/roaster-resolution.md for deriving them from a real corpus.

    Args:
        raw_names: every roaster spelling in the corpus, repeats included.
            Frequency is what selects the canonical name.
        locations: raw name -> that roaster's location strings. A location
            conflict vetoes a merge; a location match never causes one.
        decisions: adjudicated pairs, applied in both directions and never
            re-queued.
        auto_threshold: merge at or above this score.
        review_threshold: queue for review at or above this score.
        location_review_threshold: also queue pairs scoring below
            `review_threshold` when their locations match exactly.

    Returns:
        (crosswalk, review_queue). The crosswalk carries `chain_risk` and
        `violates_decision` columns marking the two known failure modes. Both
        flag clusters for inspection rather than correcting them.
    """
    counts = Counter(raw_names)  # frequency drives canonical selection
    uniques = sorted(counts)  # index space for the DSU
    keys = [core_key(n) for n in uniques]

    # -- Stage A: exact core-key collision -----------------------------------
    # No threshold and no judgment involved. This catches the bulk of the
    # variation -- suffix drift, casing, punctuation, word order -- because
    # normalization has already erased those differences.
    places = locations or {}

    def evidence(i: int, j: int) -> LocationEvidence:
        return compare_locations(places.get(uniques[i]), places.get(uniques[j]))

    verdicts = {d.key: d.verdict for d in (decisions or ())}

    def verdict_for(i: int, j: int) -> Verdict | None:
        first, second = sorted((uniques[i], uniques[j]))
        return verdicts.get((first, second))

    dsu = DSU(len(uniques))
    by_key: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        by_key[k].append(i)

    # An identical core key can still be two companies: "Direct Coffee" and
    # "Coffee Bean Direct" both reduce to "direct". Nothing in the strings
    # separates them, so the location veto applies at this stage as well as to
    # the fuzzy one.
    for group in by_key.values():
        for a_idx, i in enumerate(group):
            for j in group[a_idx + 1 :]:
                if verdict_for(i, j) is Verdict.SPLIT:
                    continue
                if evidence(i, j) is LocationEvidence.CONFLICT:
                    continue
                dsu.union(i, j)

    # -- Stage B: fuzzy scoring ----------------------------------------------
    # Handles what Stage A missed: typos and word-order drift that survived
    # normalization. Scoring runs over the distinct core keys rather than the
    # raw names, so the O(n^2) is over a smaller n and compares signal rather
    # than boilerplate.
    distinct_keys = sorted(by_key)

    # The subset guard in score() needs to know which tokens are generic in
    # this corpus, so the statistic is bound to the scorer before any
    # comparison happens. Every scoring path below uses `scorer`; a bare
    # `score` call would revert to the conservative no-corpus behaviour.
    token_df = token_document_frequency(distinct_keys)
    scorer = partial(score, token_df=token_df)

    # `workers` is not set: rapidfuzz parallelizes only its own native
    # scorers, so with a Python callable the argument is a no-op. This is
    # O(n^2) Python calls, about 3s at 1.6k keys, growing quadratically.
    matrix = process.cdist(
        distinct_keys,
        distinct_keys,
        scorer=scorer,
        score_cutoff=review_threshold,  # advisory only for custom scorers
    )

    review_rows = []
    for i in range(len(distinct_keys)):
        for j in range(i + 1, len(distinct_keys)):  # upper triangle only
            s = matrix[i][j]

            # cdist honours score_cutoff for built-in scorers but merely
            # passes it through to custom ones, where it lands in **kwargs and
            # is ignored. The cutoff is therefore enforced here; without it
            # every pair in the matrix, down to score 8, reaches the queue.
            if s < review_threshold and not (
                location_review_threshold is not None
                and s >= location_review_threshold
                and evidence(by_key[distinct_keys[i]][0], by_key[distinct_keys[j]][0])
                is LocationEvidence.SAME
            ):
                continue

            ki, kj = distinct_keys[i], distinct_keys[j]
            a, b = by_key[ki][0], by_key[kj][0]

            # An adjudicated pair is never re-decided and never re-asked.
            decided = verdict_for(a, b)
            if decided is Verdict.MERGE:
                dsu.union(a, b)
                continue
            if decided is Verdict.SPLIT:
                continue

            # A region conflict vetoes the merge. The reverse is not
            # symmetric: a matching location surfaces a pair for review and
            # never merges one, because the score does not separate a true
            # match at 82.1 from unrelated companies sharing a city at 82.9.
            # See docs/roaster-resolution.md.
            place = evidence(a, b)
            if place is LocationEvidence.CONFLICT:
                continue

            if s >= auto_threshold:
                dsu.union(a, b)
            else:
                review_rows.append(
                    {
                        "name_a": uniques[a],
                        "name_b": uniques[b],
                        # The keys are reported so that a pair's score can be
                        # traced back to what was actually compared.
                        "core_a": ki,
                        "core_b": kj,
                        "score": round(float(s), 1),
                        "location_a": "; ".join(sorted(places.get(uniques[a]) or [])),
                        "location_b": "; ".join(sorted(places.get(uniques[b]) or [])),
                        "location_evidence": str(place),
                        "verdict": "",  # filled in during review
                    }
                )

    # -- Stage C: assemble clusters ------------------------------------------
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(uniques)):
        clusters[dsu.find(i)].append(i)

    rows = []
    for cid, (_root, members) in enumerate(sorted(clusters.items())):
        names = [uniques[m] for m in members]

        # The most frequent spelling in the source data becomes canonical,
        # with ties broken by length on the assumption that the longer form is
        # the more complete one. This treats the most common spelling as the
        # correct one, which holds for most scraped data but not all; an
        # override table applied afterwards is the escape hatch.
        canonical = max(names, key=lambda n: (counts[n], len(n)))

        # Chain detection (see the DSU docstring). Single-linkage can fuse A
        # and C through B, so each cluster's worst internal pairwise score is
        # recomputed: if the weakest pair still clears auto_threshold, no
        # chaining occurred. Only meaningful above size 2, since a pair's only
        # internal score is the one that already passed.
        if len(names) > 2:
            ks = [core_key(n) for n in names]
            worst = min(
                scorer(ks[a], ks[b])
                for a in range(len(ks))
                for b in range(a + 1, len(ks))
            )
        else:
            worst = 100.0

        for n in names:
            rows.append(
                {
                    "raw_name": n,
                    "n_records": counts[n],
                    "cluster_id": cid,
                    "canonical_name": canonical,
                    "cluster_size": len(names),
                    "min_internal_score": round(float(worst), 1),
                    "chain_risk": worst < auto_threshold,
                }
            )

    # -- Stage D: split decisions defeated transitively ----------------------
    # Blocking a union is not the same as keeping two names apart. Single
    # linkage can rejoin them through a third name: "RND" and "Red Rooster
    # Coffee Roaster" are bridged by the collaboration "RND & Red Rooster
    # Coffee Roaster", whose key is a superset of both, so the split holds
    # pairwise but not in the result.
    #
    # Preventing this would mean changing the clustering; reporting it is the
    # same trade the chain_risk flag makes. Recording a split against the
    # bridging name resolves it.
    cluster_of = {
        uniques[m]: cid
        for cid, (_root, members) in enumerate(sorted(clusters.items()))
        for m in members
    }
    violated_pairs = [
        (d.name_a, d.name_b)
        for d in (decisions or ())
        if d.verdict is Verdict.SPLIT
        and d.name_a in cluster_of
        and cluster_of[d.name_a] == cluster_of.get(d.name_b)
    ]
    violated_clusters = {cluster_of[a] for a, _ in violated_pairs}
    for row in rows:
        row["violates_decision"] = row["cluster_id"] in violated_clusters

    # Sorted largest-cluster-first, since the largest clusters carry the most
    # risk and are the ones to inspect before trusting a run.
    crosswalk = pd.DataFrame(rows).sort_values(
        ["cluster_size", "cluster_id", "n_records"],
        ascending=[False, True, False],
    )
    review = (
        pd.DataFrame(review_rows).sort_values("score", ascending=False)
        if review_rows
        else pd.DataFrame(columns=REVIEW_COLUMNS)
    )
    return crosswalk, review
