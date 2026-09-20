"""Entity resolution for messy coffee roaster names.

Scraped data spells one roaster several ways -- "Onyx Coffee Lab", "Onyx Coffee
Lab LLC", "onyx coffee lab". This module groups those spellings, picks a
canonical one, and persists the mapping.

Resolution runs as a cascade, cheapest and most certain stage first:

    normalize -> exact key -> location veto -> fuzzy -> human review

Precision is favoured over recall throughout, since a false merge is silent
while a false split is visible. This accounts for the conservative behaviour:
a location conflict vetoes a merge but a location match never causes one, the
uncertain band is queued for review rather than decided, and the two known
clustering failure modes are reported rather than prevented.

Adjudicated pairs live in ``roaster_decisions.csv``, the only state here that
cannot be regenerated. The crosswalk and the review queue are derived on every
run.

``docs/roaster-resolution.md`` covers the design in full. The README section
"Resolving roaster names" covers the workflow.
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from functools import partial
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

__all__ = [
    "ABBREV",
    "DECISION_COLUMNS",
    "Decision",
    "LocationEvidence",
    "MAX_SUBSET_TOKEN_DF",
    "REVIEW_COLUMNS",
    "STOPWORDS",
    "Verdict",
    "compare_locations",
    "core_key",
    "fingerprint",
    "load_decisions",
    "normalize_location",
    "parse_verdict",
    "promote_reviewed",
    "resolve",
    "save_decisions",
    "score",
    "strip_accents",
    "token_document_frequency",
    "tokens",
    "unpromoted_verdicts",
]

# ==========================================================================
# 1. NORMALIZATION
# ==========================================================================

# Words shared by most roaster names, which therefore say little about which
# roaster a name refers to. Removing them before measuring distance collapses
# "Stumptown" and "Stumptown Coffee Roasters" to the same key, so they match on
# an exact lookup rather than on a threshold.
#
# Misspellings of the stopwords belong here too ("coffe", "cofee"); they would
# otherwise survive into the key as noise tokens.
# fmt: off
# Grouped by kind; the grouping is what each line documents. Keep the formatter
# from flattening it to one word per line.
STOPWORDS = {
    "coffee", "coffees", "coffe", "cofee",
    "roaster", "roasters", "roasting", "roastery", "roasterie",
    "cafe", "caffe", "kaffee", "espresso", "bean", "beans",
    "co", "company", "inc", "incorporated", "llc", "ltd", "limited", "corp",
    "the", "and",
}
# fmt: on

# Applied BEFORE stopword removal, so that whatever an abbreviation expands to
# can itself be stopworded if it belongs on the list above.
ABBREV = {
    "bros": "brothers",
    "bro": "brothers",
    "mfg": "manufacturing",
    "intl": "international",
    "st": "saint",
    "mt": "mount",
}


def strip_accents(s: str) -> str:
    """Café -> Cafe.

    NFKD splits an accented character into a base plus combining marks, which
    are then dropped. Scraped pages carry both spellings of the same roaster
    depending on which page a name came from.
    """
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def tokens(name: str) -> list[str]:
    """Raw name -> clean token list.

    Each step closes one source of spelling noise, and the order matters. Two
    are easy to undo by accident:

    1. Apostrophes are deleted rather than turned into spaces, so "Peet's"
       reaches the same token as "Peets". Splitting on the apostrophe would
       yield ["peet", "s"] and a stray single letter.
    2. Runs of single letters are collapsed. "J.B.C. Coffee Roasters"
       punctuation-strips to ["j", "b", "c", ...], which shares nothing with
       "JBC Coffee" -> ["jbc", ...]. Gluing the run back together makes an
       initialism agree with its solid form.
    """
    s = strip_accents(str(name)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"['\u2019]", "", s)
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    toks = [ABBREV.get(t, t) for t in s.split()]

    out: list[str] = []
    run: list[str] = []
    for t in toks:
        if len(t) == 1 and t.isalpha():
            run.append(t)
        else:
            if run:
                out.append("".join(run))
                run = []
            out.append(t)
    if run:
        out.append("".join(run))
    return out


def fingerprint(name: str) -> str:
    """OpenRefine's key-collision fingerprint, reimplemented.

    Dedupe + sort the tokens, so word order stops mattering. Kept as a distinct
    function because it's the safe fallback when stopwording is too aggressive
    (see core_key).
    """
    return " ".join(sorted(set(tokens(name))))


def core_key(name: str) -> str:
    """Fingerprint with domain stopwords removed. The workhorse.

        "Stumptown"                 -> "stumptown"
        "Stumptown Coffee"          -> "stumptown"
        "Stumptown Roasters"        -> "stumptown"
        "Stumptown Coffee Roasters" -> "stumptown"

    A name composed entirely of stopwords, such as "The Coffee Company",
    reduces to nothing; it falls back to the full fingerprint so that it keeps
    an identity. Such a name still matches poorly against its own variants.
    """
    toks = sorted({t for t in tokens(name) if t not in STOPWORDS})
    if not toks:
        return fingerprint(name)
    return " ".join(toks)


# ==========================================================================
# 2. SIMILARITY
# ==========================================================================

# A one-token core key is trusted as a subset match only when that token is
# rare across the corpus. See the subset guard in score().
MAX_SUBSET_TOKEN_DF = 2


def token_document_frequency(keys: Iterable[str]) -> Counter[str]:
    """How many distinct core keys each token appears in.

    Separates a generic token such as "kona" (15 keys) from an identifying one
    such as "stumptown" (1). Measured from the corpus, so it adapts to whatever
    stopwording leaves behind.
    """
    return Counter(tok for key in keys for tok in set(key.split()))


def score(a: str, b: str, token_df: Mapping[str, int] | None = None, **kwargs) -> float:
    """Similarity of two core keys (not raw names) in [0, 100].

    Two kinds of variation survive normalization, and no single metric covers
    both, so the result is the maximum of two:

      token_set_ratio   Subset relationships. "Onyx Coffee" -> "onyx" and
                        "Onyx Coffee Lab" -> "lab onyx"; one key is a strict
                        subset of the other, which token_set scores 100.

      token_sort_ratio  Typos and reordering. Sorts tokens, then edit distance.

    Taking the maximum is permissive: a pair becomes a candidate if either
    metric rates it a match. That is affordable because resolve() applies
    strict thresholds and because of the subset guard below.

    Subset guard
        token_set scores any strict subset 100 regardless of how little the
        shorter key says, and stopwording produces bare generic keys that then
        match everything: "Kona Cafe" -> "kona", "Coffee Bros." -> "brothers".
        Unguarded, each such key merges with every key containing it and
        union-find chains the neighbourhood into a single cluster; on the
        current corpus that placed 219 of 1,591 spellings in chained clusters.

        A subset reading is therefore trusted only when the shorter key is
        distinctive: at least 2 tokens, or a single token that is rare in the
        corpus (document frequency <= MAX_SUBSET_TOKEN_DF). The property that
        matters is genericness rather than length -- "international" is long
        and uninformative, "coffeeam" is short and identifying.

    `token_df` comes from token_document_frequency(). When it is None, as in
    direct calls and tests, one-token keys are not trusted for subset matching.

    **kwargs absorbs the `score_cutoff` that rapidfuzz.process.cdist injects
    into scorer callables; without it, cdist raises TypeError.
    """
    shorter = a if len(a) <= len(b) else b
    shorter_tokens = shorter.split()

    if len(shorter_tokens) >= 2:
        subset_is_trustworthy = True
    elif not shorter_tokens or token_df is None:
        subset_is_trustworthy = False
    else:
        df = token_df.get(shorter_tokens[0], MAX_SUBSET_TOKEN_DF + 1)
        subset_is_trustworthy = df <= MAX_SUBSET_TOKEN_DF

    result = float(fuzz.token_sort_ratio(a, b))
    if subset_is_trustworthy:
        result = max(result, float(fuzz.token_set_ratio(a, b)))
    return result


# ==========================================================================
# 3. LOCATION: THE SECOND SIGNAL
# ==========================================================================

# Names alone leave a wide band of uncertainty. Roaster location narrows it:
# the field is populated on nearly every review and is close to orthogonal to
# spelling, so it carries information the string score does not. On the current
# review queue it settled 41 of 50 pairs -- "Heart Coffee Roasters" (Portland,
# Oregon) against "Heat Coffee" (Taipei, Taiwan) scores 88.9 on name alone.
#
# Location is evidence rather than proof, and the two directions differ in
# strength:
#   different region  near-decisive that these are different companies
#   identical place   confirmatory, but "Bear Coffee" and "Bear Coffee
#                     Roasters" in one city could still be two businesses
# A region conflict therefore blocks a merge, while an exact match only
# surfaces the pair for review.


class LocationEvidence(StrEnum):
    """What the two names' locations say about whether they are one company."""

    SAME = "same"  # identical place: surface for review
    CONFLICT = "conflict"  # no region in common: refuse to merge
    NEUTRAL = "neutral"  # same region, different city: no opinion
    UNKNOWN = "unknown"  # at least one side has no location


def normalize_location(value: str) -> tuple[str | None, str | None]:
    """``"London, Ontario, Canada"`` -> ``("london", "canada")``.

    Returns (city, region). CoffeeReview writes locations most-specific-first,
    so the last comma-separated part is the region or country and the first is
    the city. A single-part value such as "El Salvador" is a region with no
    city.
    """
    parts = [strip_accents(part).strip().lower() for part in str(value).split(",")]
    parts = [part for part in parts if part]
    if not parts:
        return None, None
    if len(parts) == 1:
        return None, parts[0]
    return parts[0], parts[-1]


def compare_locations(
    places_a: Iterable[str] | None, places_b: Iterable[str] | None
) -> LocationEvidence:
    """Weigh two names' location sets against each other.

    Each name carries a set of locations rather than one, because a roaster can
    appear at several over the years; 155 of 1,591 in the current data do.
    Comparing sets keeps a relocation from reading as a conflict, since any
    overlap withholds the veto.
    """
    norm_a = {normalize_location(p) for p in places_a or () if p}
    norm_b = {normalize_location(p) for p in places_b or () if p}
    norm_a.discard((None, None))
    norm_b.discard((None, None))
    if not norm_a or not norm_b:
        return LocationEvidence.UNKNOWN

    if norm_a & norm_b:
        return LocationEvidence.SAME

    regions_a = {region for _, region in norm_a if region}
    regions_b = {region for _, region in norm_b if region}
    if regions_a and regions_b and not (regions_a & regions_b):
        return LocationEvidence.CONFLICT
    return LocationEvidence.NEUTRAL


# ==========================================================================
# 4. DECISIONS
# ==========================================================================

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


# ==========================================================================
# 5. UNION-FIND (DISJOINT SET)
# ==========================================================================


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
