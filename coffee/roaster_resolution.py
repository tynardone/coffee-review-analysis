"""Entity resolution for messy coffee roaster names.

Scraped data spells one roaster several ways -- "Onyx Coffee Lab", "Onyx Coffee
Lab LLC", "onyx coffee lab". This module groups those spellings, picks a
canonical one, and persists the mapping.

Resolution is a cascade, cheapest and most certain first:

    normalize -> exact key -> location veto -> fuzzy -> human/LLM review

It optimises for PRECISION over recall, because a false merge is silent while a
false split is loud. That one asymmetry explains most of what looks conservative
here: location conflict vetoes a merge but a location match never causes one,
the uncertain band goes to a review queue instead of being decided, and the two
ways clustering is known to go wrong are reported rather than prevented.

Adjudicated pairs live in ``roaster_decisions.csv`` and are the only state here
that cannot be regenerated; the crosswalk and the review queue are derived on
every run.

The reasoning in full -- the error asymmetry, the location signal, why there are
two thresholds and how to tune them -- is in ``docs/roaster-resolution.md``. The
workflow for actually running this is in the README under "Resolving roaster
names".
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

# Words that carry near-zero information about *which* roaster this is, because
# nearly every roaster has some subset of them. Removing them before measuring
# distance means the distance is measured over signal only -- and it collapses
# "Stumptown" and "Stumptown Coffee Roasters" to the SAME string, so they match
# on an exact lookup with no threshold involved. (See the design doc; this is
# why normalization matters more here than the choice of algorithm.)
#
# TUNING: this list is the first thing to edit for new data. Misspellings of
# the stopwords themselves belong here too ("coffe", "cofee") -- they would
# otherwise survive into the key as noise tokens.
# fmt: off
# Grouped by kind deliberately -- the grouping documents what each line is for.
# Keep the formatter from flattening it to one word per line.
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

    NFKD splits an accented char into base + combining mark; we drop the marks.
    Necessary because a scraper will happily give you both spellings of the same
    roaster depending on which page it hit.
    """
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def tokens(name: str) -> list[str]:
    """Raw name -> clean token list.

    Most of the debugging in this problem is at the CHARACTER level, not the
    algorithm level. Each line below closes a specific noise channel, and the
    order matters. Two are landmines worth stating outright:

    1. APOSTROPHES ARE DELETED, not turned into spaces. "Peet's" has to reach
       the same token as "Peets"; splitting on the apostrophe would instead
       yield ["peet", "s"] and a stray single letter.
    2. SINGLE-LETTER RUNS ARE COLLAPSED.
       "J.B.C. Coffee Roasters" punctuation-strips to ["j","b","c",...], which
       shares nothing with "JBC Coffee" -> ["jbc",...]. Gluing runs of single
       letters back together makes initialisms agree with their solid form.
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

    FAILURE MODE: a roaster genuinely named "The Coffee Company" stopwords down
    to nothing. We fall back to the full fingerprint so it at least keeps an
    identity. This is a patch, not a solution — such a name will also match
    poorly against its own variants. Rare enough in practice to accept, but know
    it's here.
    """
    toks = sorted({t for t in tokens(name) if t not in STOPWORDS})
    if not toks:
        return fingerprint(name)
    return " ".join(toks)


# ==========================================================================
# 2. SIMILARITY
# ==========================================================================

# A one-token core key is trusted as a subset match only when that token is
# rare across the corpus. See THE SUBSET GUARD in score().
MAX_SUBSET_TOKEN_DF = 2


def token_document_frequency(keys: Iterable[str]) -> Counter[str]:
    """How many DISTINCT core keys each token appears in.

    Separates "kona" (15 keys, generic here) from "stumptown" (1, identifying).
    Measured from the data, so it adapts to whatever stopwording leaves behind.
    """
    return Counter(tok for key in keys for tok in set(key.split()))


def score(a: str, b: str, token_df: Mapping[str, int] | None = None, **kwargs) -> float:
    """Similarity of two CORE KEYS (not raw names) in [0, 100].

    Two kinds of variation survive normalization, and no single metric handles
    both — hence the max of two:

      token_set_ratio   SUBSET relationships. "Onyx Coffee" -> "onyx" and
                        "Onyx Coffee Lab" -> "lab onyx"; one key is a strict
                        subset of the other, which token_set scores 100.

      token_sort_ratio  TYPOS and reordering. Sorts tokens, then edit distance.

    The max is deliberately permissive — "if either view thinks these are the
    same, treat them as candidates" — which is only affordable because the
    thresholds in resolve() are strict, and because of the guard below.

    THE SUBSET GUARD
        token_set scores ANY strict subset 100, however little it says, and
        stopwording MANUFACTURES bare generic keys that then match everything:
        "Kona Cafe" -> "kona", "Coffee Bros." -> "brothers". Unguarded on the
        real data, each such key auto-merged with every key containing it and
        union-find chained the neighborhood into one cluster — 219 of 1,591
        spellings ended up in chained clusters.

        So trust a subset reading only when the shorter key is DISTINCTIVE:
        >= 2 tokens, or a single token rare in the corpus (document frequency
        <= MAX_SUBSET_TOKEN_DF). Genericness, not length, is the property that
        matters — "international" is long and useless, "coffeeam" is short and
        identifying.

    `token_df` comes from token_document_frequency(). When it is None (direct
    calls, tests) one-token keys are not trusted for subset matching, which is
    the conservative reading per the asymmetry at the top of this file.

    **kwargs absorbs the `score_cutoff` that rapidfuzz.process.cdist injects
    into scorer callables. Without it, cdist raises TypeError.
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
# 3. LOCATION — THE SECOND SIGNAL
# ==========================================================================

# Names alone leave a wide band of honest uncertainty. Roaster location closes
# most of it: it is populated on essentially every review, and it is nearly
# orthogonal to spelling, so it carries information the string score cannot.
#
# On the real review queue it resolved 41 of 50 pairs outright — "Heart Coffee
# Roasters" (Portland, Oregon) against "Heat Coffee" (Taipei, Taiwan) scores
# 88.9 on name alone and is obviously not the same company.
#
# It is EVIDENCE, NOT PROOF, and the two directions are not symmetric:
#   different region  -> near-decisive that these are different companies
#   identical place   -> strongly confirmatory, but "Bear Coffee" and "Bear
#                        Coffee Roasters" in one city could still be two shops
# So a region conflict BLOCKS a merge, while an exact match only LOWERS THE BAR
# rather than forcing one.


class LocationEvidence(StrEnum):
    """What the two names' locations say about whether they are one company."""

    SAME = "same"  # identical place -> lower the bar
    CONFLICT = "conflict"  # no region in common -> refuse to merge
    NEUTRAL = "neutral"  # same region, different city -> no opinion
    UNKNOWN = "unknown"  # at least one side has no location


def normalize_location(value: str) -> tuple[str | None, str | None]:
    """``"London, Ontario, Canada"`` -> ``("london", "canada")``.

    Returns (city, region). CoffeeReview writes locations most-specific-first,
    so the LAST comma-separated part is the region or country and the first is
    the city. A single-part value ("El Salvador") is a region with no city.
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

    Each name gets a SET of locations, not one, because a roaster legitimately
    appears at several over the years — 155 of 1,591 in the current data do.
    Comparing sets rather than single values keeps a relocation from reading as
    a conflict: any overlap is enough to withhold the veto.
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
# 4. DECISIONS — DURABLE HUMAN JUDGEMENT
# ==========================================================================

# The crosswalk is DERIVED and regenerable; these are the inputs that are not.
# Keeping them in a separate file is what lets manual effort accumulate instead
# of resetting: every rerun re-derives the clusters, but never re-asks a
# question already answered.


class Verdict(StrEnum):
    MERGE = "merge"
    SPLIT = "split"


# What counts as an answer in the queue's `verdict` column.
#
# Being strict here is a bad trade. The column is filled in by hand, often in a
# spreadsheet, and "y"/"n" is the obvious thing to type when the question is
# "are these the same company?". Accepting only the two canonical spellings
# meant a whole afternoon of answers was silently skipped.
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
    """One adjudicated pair. `decided_by` records who or what decided.

    Attribution matters because the review band is where an LLM is worth using,
    and LLM calls should be revisitable as a group without disturbing your own.
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

# Columns of the review queue. `verdict` is the one you fill in; the location
# columns are there so the evidence is visible without a second lookup.
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

    This is the step that closes the loop. Without it the queue is a form nobody
    collects: you can fill in `verdict`, but the next run re-derives the queue
    from scratch and asks again. Promoting the answers is what makes the effort
    cumulative — every question is asked at most once, ever.

    Blank rows are left alone, so a partially filled queue is fine. A value
    that cannot be read is reported rather than skipped in silence — silently
    dropping answers is indistinguishable from the tool not working, and costs
    whoever filled the queue in their whole session.

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
        # An existing verdict is never silently overwritten; edit the decisions
        # file directly to change your mind, so the change is visible in git.
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
    """Turns PAIRS into GROUPS: link every pair above threshold, then read off
    the connected components.

    This is single-linkage clustering, so merges are transitive: A~B and B~C
    puts A and C together even if they would score 40 against each other. Kept
    deliberately, because the chaining is DETECTABLE -- resolve() flags each
    cluster's worst internal score as `chain_risk`. See the "Known limits"
    section of docs/roaster-resolution.md for when to trade it for complete
    linkage instead.
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

    Two thresholds, not one, because there is a real middle band where the
    STRINGS DO NOT CONTAIN THE ANSWER -- "Black Oak Coffee Roasters" vs
    "Black & White Coffee Roasters" scores 71, and only world knowledge says
    they are different companies:

        score >= auto_threshold      merge automatically
        review..auto                 honest uncertainty -> review queue
        score <  review_threshold    leave alone

    The defaults (92/82) are tuned on a toy set. See docs/roaster-resolution.md
    for how to derive real ones, and for why the review band is the only place
    an LLM is worth applying to this problem.

    Args:
        raw_names: every roaster spelling in the corpus, repeats included --
            frequency is what picks the canonical name.
        locations: raw name -> that roaster's location strings. A location
            CONFLICT vetoes a merge; a location match never causes one.
        decisions: adjudicated pairs. Applied in both directions and never
            re-queued, which is what makes manual effort accumulate.
        auto_threshold: merge at or above this score.
        review_threshold: queue for review at or above this score.
        location_review_threshold: also queue pairs scoring below
            `review_threshold` when their locations match exactly.

    Returns:
        (crosswalk, review_queue). The crosswalk carries `chain_risk` and
        `violates_decision` columns flagging the two known failure modes;
        both are alarms, not guarantees, and are meant to be triaged.
    """
    counts = Counter(raw_names)  # frequency drives canonical selection
    uniques = sorted(counts)  # index space for the DSU
    keys = [core_key(n) for n in uniques]

    # -- Stage A: exact core-key collision -----------------------------------
    # Free and ~100% precise. No threshold, no judgment. This catches the bulk
    # of real-world variation (suffix drift, casing, punctuation, word order)
    # because normalization already erased exactly those differences.
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

    # Even an identical core key can be two companies: "Direct Coffee" and
    # "Coffee Bean Direct" both stopword down to "direct". Nothing in the
    # strings can separate them, which is why the location veto applies here
    # too and not only to the fuzzy stage.
    for group in by_key.values():
        for a_idx, i in enumerate(group):
            for j in group[a_idx + 1 :]:
                if verdict_for(i, j) is Verdict.SPLIT:
                    continue
                if evidence(i, j) is LocationEvidence.CONFLICT:
                    continue
                dsu.union(i, j)

    # -- Stage B: fuzzy scoring ----------------------------------------------
    # Only mops up what Stage A missed: typos and word-order drift that survived
    # normalization. Note we score the DISTINCT CORE KEYS, not the raw names —
    # so the O(n^2) is over a much smaller n, and the comparison is over signal
    # rather than boilerplate.
    distinct_keys = sorted(by_key)

    # The subset guard in score() needs to know which tokens are generic IN THIS
    # CORPUS, so bind the statistic to the scorer before any comparison happens.
    # Every scoring path below must use `scorer`, never bare `score` — an
    # unbound call silently reverts to the conservative no-corpus behavior.
    token_df = token_document_frequency(distinct_keys)
    scorer = partial(score, token_df=token_df)

    # NOTE: `workers` is deliberately not set. rapidfuzz can only parallelize its
    # own native scorers; with a Python callable it is a no-op, so asking for it
    # only implies a speed that isn't there. This is O(n^2) Python calls — ~3s at
    # 1.6k keys, and it grows quadratically.
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

            # GOTCHA: cdist's score_cutoff is honored by BUILT-IN scorers but is
            # merely passed through to custom ones (it lands in our **kwargs and
            # we ignore it). So the cutoff must be enforced here by hand — omit
            # this and every pair in the matrix, down to score 8, floods
            # review.csv.
            if s < review_threshold and not (
                location_review_threshold is not None
                and s >= location_review_threshold
                and evidence(by_key[distinct_keys[i]][0], by_key[distinct_keys[j]][0])
                is LocationEvidence.SAME
            ):
                continue

            ki, kj = distinct_keys[i], distinct_keys[j]
            a, b = by_key[ki][0], by_key[kj][0]

            # An adjudicated pair is never re-decided and never re-asked. This
            # is what makes manual effort accumulate rather than reset.
            decided = verdict_for(a, b)
            if decided is Verdict.MERGE:
                dsu.union(a, b)
                continue
            if decided is Verdict.SPLIT:
                continue

            # A region conflict vetoes the merge outright. The reverse is
            # deliberately NOT symmetric: a matching location SURFACES a pair
            # for review, it never merges one.
            #
            # Merging on a lowered bar was tried and is unsafe: the score does
            # not separate a true match at 82.1 from unrelated companies
            # sharing a city at 82.9. See docs/roaster-resolution.md.
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
                        "core_a": ki,  # keys are shown so you can see WHY
                        "core_b": kj,  # a pair scored the way it did
                        "score": round(float(s), 1),
                        "location_a": "; ".join(sorted(places.get(uniques[a]) or [])),
                        "location_b": "; ".join(sorted(places.get(uniques[b]) or [])),
                        "location_evidence": str(place),
                        "verdict": "",  # <- you (or an LLM) fill in merge/split
                    }
                )

    # -- Stage C: assemble clusters ------------------------------------------
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(uniques)):
        clusters[dsu.find(i)].append(i)

    rows = []
    for cid, (_root, members) in enumerate(sorted(clusters.items())):
        names = [uniques[m] for m in members]

        # CANONICAL SELECTION: most frequent spelling in the source data wins;
        # ties broken by length (longer = more complete form).
        #
        # ASSUMPTION: the most common spelling is the correct one. Usually true
        # in scraped data, not always. When it picks something ugly, don't fight
        # the heuristic — add a canonical_overrides.csv and apply it afterward.
        canonical = max(names, key=lambda n: (counts[n], len(n)))

        # CHAIN DETECTION (see DSU docstring). Single-linkage can fuse A and C
        # via B. Recompute the WORST pairwise score inside each cluster: if even
        # the weakest internal pair clears auto_threshold, no chaining occurred.
        # If it doesn't, this cluster was assembled transitively — look at it.
        # Only meaningful for size > 2; a 2-cluster's only pair is the one that
        # already passed.
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
                    "chain_risk": worst < auto_threshold,  # <- triage on this first
                }
            )

    # -- Stage D: did any split decision get defeated transitively? ----------
    # Blocking a union is not the same as keeping two names apart. Single
    # linkage can rejoin them through a third name -- "RND" and "Red Rooster
    # Coffee Roaster" are bridged by the collaboration "RND & Red Rooster
    # Coffee Roaster", whose key is a superset of both. The split is then
    # honoured pairwise and violated in the result.
    #
    # This cannot be prevented without changing the clustering, but it CAN be
    # reported, which is the same bargain the chain_risk alarm makes: a cheap
    # algorithm plus a loud alarm beats an expensive one with none. A violation
    # is fixed by splitting the bridging name as well.
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

    # Sorted biggest-cluster-first: the largest clusters carry the most risk and
    # are what you want to eyeball before trusting the run.
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
