"""
Entity resolution for messy coffee roaster names.

THE PROBLEM
    Scraped review data spells the same roaster several ways:
        "Onyx Coffee Lab" / "Onyx Coffee Lab LLC" / "onyx coffee lab" / "Onyx Coffee"
    Group them, pick one canonical spelling, and persist the mapping.

THE GOVERNING ASYMMETRY
    The two error types are NOT equally costly, and every decision below is
    bought with that fact:

      False MERGE  (Black Oak + Black & White -> one roaster)  is SILENT.
          Downstream analysis runs fine. Scores for "Black Oak" are now a
          blend of two companies and you will never notice.

      False SPLIT  ("Stumptown" and "Stumptown Coffee" stay separate)  is LOUD.
          Stumptown shows up twice in your top-20 table. You catch it instantly.

    So: optimize for PRECISION, not recall. Leave merges on the table rather
    than make a wrong one. Recall failures announce themselves; precision
    failures don't. (This is also why OpenRefine's UI is risky — approving a
    cluster is one click, and all the friction sits on the *reject* side,
    exactly backwards from where the risk lives.)

THE CASCADE
    Cheapest + most certain first; expensive + most doubtful last. Each stage
    shrinks the input to the next, so by the time we reach the part that can be
    wrong, there is very little left for it to be wrong about.

        normalize  ->  exact key collision  ->  fuzzy score  ->  human/LLM review
          free           free, ~100% precise      O(n^2), fallible     expensive

OUTPUTS
    crosswalk.csv   raw_name -> canonical_name.  THE DELIVERABLE. Commit to git.
                    Next scrape, left-join against this: already-resolved names
                    cost nothing and only NEW spellings reach the review queue.
                    Manual effort per run decays toward zero instead of
                    resetting to full every time (which is what OpenRefine does).

    review.csv      Pairs in the honest-uncertainty band, with a blank `merge`
                    column for you (or an LLM) to fill in.


USAGE
    resolve-roasters names.csv --column roaster --outdir ./out
"""

from __future__ import annotations

import re
import unicodedata
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from functools import partial

import pandas as pd
from rapidfuzz import fuzz, process

# ==========================================================================
# 1. NORMALIZATION
# ==========================================================================

# Words that carry near-zero information about *which* roaster this is, because
# nearly every roaster has some subset of them. Deleting them before measuring
# distance means the distance we measure is over signal only.
#
# Why this matters more than any algorithm choice:
#   Under plain edit distance, "Stumptown" vs "Stumptown Coffee Roasters" is 15
#   edits on a 24-char string — you'd need a threshold so loose it would merge
#   half the dataset. Strip these words and BOTH become the string "stumptown".
#   Not "similar". IDENTICAL. They now collide on an exact hash lookup, which
#   has ~100% precision by construction: no threshold to tune, no judgment to
#   get wrong. A large fraction of the problem is solved for free, right here.
#
# TUNING: this list is the first thing to edit for your data. Misspellings of
# the stopwords themselves belong here too ("coffe", "cofee") — they'd
# otherwise survive into the key as noise tokens.
# fmt: off
# Grouped by kind deliberately — the grouping is the documentation for what
# each line is doing. Keep the formatter from flattening it to one per line.
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

    1. APOSTROPHES ARE DELETED.
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
# 3. UNION-FIND (DISJOINT SET)
# ==========================================================================


class DSU:
    """Turns PAIRS into GROUPS: link every pair above threshold, then read off
    the connected components.

    THE COST — CHAINING. This is single-linkage clustering, so merges are
    transitive by construction: if A~B at 93 and B~C at 93, then A and C land in
    the same cluster even if they'd score 40 against each other. Classic failure
    mode, and exactly what OpenRefine's nearest-neighbor clustering does too.

    WHY KEEP IT ANYWAY. Complete-linkage hierarchical clustering is stricter but
    not *correct* — it just trades one error set for another. Meanwhile chaining
    is DETECTABLE: resolve() computes each cluster's worst internal pairwise
    score and flags it (`chain_risk`). Cheap algorithm + an alarm bell beats an
    expensive algorithm and no alarm bell. Detection beats prevention when
    prevention costs you something.

    If chain_risk lights up frequently on your real data, THEN swap in complete
    linkage (scipy.cluster.hierarchy with method='complete'). Not before.
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
# 4. RESOLUTION
# ==========================================================================


def resolve(
    raw_names: list[str],
    auto_threshold: int = 92,
    review_threshold: int = 82,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Cluster raw names; return (crosswalk, review_queue).

    WHY TWO THRESHOLDS, NOT ONE
        A single threshold forces a lie — it asserts every pair is either a
        match or not, with the boundary at a number you made up. But there is a
        real middle region where the STRINGS SIMPLY DO NOT CONTAIN THE ANSWER:

            "Black Oak Coffee Roasters" vs "Black & White Coffee Roasters" -> 71
            "Red Bay Coffee"            vs "Red Rooster Coffee Roaster"    -> 60

        These are not matches, but nothing in the strings says so. The only
        thing that resolves them is knowing these are four different companies.
        That's world knowledge, not string knowledge. So:

            score >= auto_threshold      merge automatically
            review..auto                 honest uncertainty -> review.csv
            score <  review_threshold    leave alone

        The review band is EXACTLY where an LLM earns its keep — and nowhere
        else. Handing a model 500 raw names and asking it to canonicalize is
        asking it to hallucinate at scale with no way to audit the result.
        Handing it 30 pre-scored ambiguous pairs and asking "same company, y/n,
        why?" is a bounded, verifiable task on precisely the cases where world
        knowledge beats the string metric — and its errors land somewhere you're
        already looking.

    THRESHOLD TUNING (the softest part of this whole design)
        92/82 are tuned on a toy set. Get real numbers for your data: run it,
        sort review.csv by score descending, and find where TRUE matches stop
        appearing. That score is your real `auto`. Then find where plausible
        matches stop appearing entirely — that's your real `review` floor.
    """
    counts = Counter(raw_names)  # frequency drives canonical selection
    uniques = sorted(counts)  # index space for the DSU
    keys = [core_key(n) for n in uniques]

    # -- Stage A: exact core-key collision -----------------------------------
    # Free and ~100% precise. No threshold, no judgment. This catches the bulk
    # of real-world variation (suffix drift, casing, punctuation, word order)
    # because normalization already erased exactly those differences.
    dsu = DSU(len(uniques))
    by_key: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        by_key[k].append(i)
    for group in by_key.values():
        for j in group[1:]:
            dsu.union(group[0], j)

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
            if s < review_threshold:
                continue

            ki, kj = distinct_keys[i], distinct_keys[j]
            if s >= auto_threshold:
                dsu.union(by_key[ki][0], by_key[kj][0])
            else:
                review_rows.append(
                    {
                        "name_a": uniques[by_key[ki][0]],
                        "name_b": uniques[by_key[kj][0]],
                        "core_a": ki,  # keys are shown so you can see WHY
                        "core_b": kj,  # a pair scored the way it did
                        "score": round(float(s), 1),
                        "merge": "",  # <- you (or an LLM) fill in y/n
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

    # Sorted biggest-cluster-first: the largest clusters carry the most risk and
    # are what you want to eyeball before trusting the run.
    crosswalk = pd.DataFrame(rows).sort_values(
        ["cluster_size", "cluster_id", "n_records"],
        ascending=[False, True, False],
    )
    review = (
        pd.DataFrame(review_rows).sort_values("score", ascending=False)
        if review_rows
        else pd.DataFrame(
            columns=["name_a", "name_b", "core_a", "core_b", "score", "merge"]
        )
    )
    return crosswalk, review
