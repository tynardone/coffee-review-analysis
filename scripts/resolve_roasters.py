"""
resolve_roasters.py — entity resolution for messy coffee roaster names.

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

BEFORE YOU USE THIS AT ALL
    Fuzzy string matching is what you do when you have been DENIED a real join
    key. If the scrape carries a roaster URL, block on the domain instead —
    `onyxcoffeelab.com` is deterministic and beats every heuristic here. Check
    for a real key first. Same for city/state, which at minimum lets you refuse
    to merge two roasters in different states.

USAGE
    python resolve_roasters.py names.csv --column roaster --outdir ./out
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz, process

# ==========================================================================
# 1. NORMALIZATION  — the stage that does the most work while looking like
#                     the least. Spend effort on the REPRESENTATION, not the
#                     metric.
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
STOPWORDS = {
    "coffee", "coffees", "coffe", "cofee",
    "roaster", "roasters", "roasting", "roastery", "roasterie",
    "cafe", "caffe", "kaffee", "espresso", "bean", "beans",
    "co", "company", "inc", "incorporated", "llc", "ltd", "limited", "corp",
    "the", "and",
}

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

    1. APOSTROPHES ARE DELETED, NOT SPACED.
       The obvious implementation — regex-replace all punctuation with a space —
       turns "Willoughby's" into the tokens ["willoughby", "s"]. That stray "s"
       then sorts to the FRONT of the key ("s willoughby") and wrecks it. This
       is the class of bug that makes people conclude "fuzzy matching doesn't
       work on my data" when in fact their tokenizer is broken. Kill the
       apostrophe before the general punctuation pass.

    2. SINGLE-LETTER RUNS ARE COLLAPSED.
       "J.B.C. Coffee Roasters" punctuation-strips to ["j","b","c",...], which
       shares nothing with "JBC Coffee" -> ["jbc",...]. Gluing runs of single
       letters back together makes initialisms agree with their solid form.
    """
    s = strip_accents(str(name)).lower()
    s = s.replace("&", " and ")                  # "Black & White" == "Black and White"
    s = re.sub(r"['\u2019]", "", s)               # LANDMINE 1 — see docstring
    s = re.sub(r"[^a-z0-9\s]", " ", s)            # all other punctuation -> space
    s = re.sub(r"\s+", " ", s).strip()
    toks = [ABBREV.get(t, t) for t in s.split()]

    # LANDMINE 2 — see docstring. "j b c" -> "jbc"
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

def score(a: str, b: str, **kwargs) -> float:
    """Similarity of two CORE KEYS (not raw names) in [0, 100].

    Two DIFFERENT KINDS of variation survive normalization, and no single metric
    handles both — hence the max of two:

      token_set_ratio   handles SUBSET relationships. After stopwording,
                        "Onyx Coffee" -> "onyx" and "Onyx Coffee Lab" -> "lab onyx".
                        One key is a strict subset of the other. token_set
                        partitions into intersection + the two remainders and
                        scores the intersection against the wholes, so a strict
                        subset scores 100.

      token_sort_ratio  handles TYPOS and reordering. Sorts tokens, then runs a
                        normal edit distance — robust to order, still sensitive
                        to character noise ("Cofee" vs "Coffee").

    Taking the max is deliberately PERMISSIVE: "if either view thinks these are
    the same, treat them as candidates." That's only affordable because the
    thresholds downstream are strict. Precision is enforced there, not here.

    ------------------------------------------------------------------------
    KNOWN HAZARD — token_set's subset behavior is a loaded gun.
    ANY key that is a strict subset of another scores 100, no matter how little
    it says. A bare key like "black" would score 100 against BOTH "black oak"
    and "black white", and union-find would then fuse all three into one
    roaster. This doesn't blow up on real roaster data because real names have
    distinctive heads. But if your data yields very short or generic residual
    keys after stopwording, add a guard here: require the shorter key to be >= 2
    tokens or >= 5 chars before trusting a subset match.
    ------------------------------------------------------------------------

    **kwargs absorbs the `score_cutoff` that rapidfuzz.process.cdist injects
    into scorer callables. Without it, cdist raises TypeError.
    """
    return max(
        fuzz.token_set_ratio(a, b),
        fuzz.token_sort_ratio(a, b),
    )


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
            self.p[x] = self.p[self.p[x]]      # path compression
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
    counts = Counter(raw_names)          # frequency drives canonical selection
    uniques = sorted(counts)             # index space for the DSU
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

    matrix = process.cdist(
        distinct_keys, distinct_keys,
        scorer=score,
        score_cutoff=review_threshold,   # advisory only for custom scorers
        workers=-1,                      # all cores; fine to ~10k keys
    )

    review_rows = []
    for i in range(len(distinct_keys)):
        for j in range(i + 1, len(distinct_keys)):   # upper triangle only
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
                review_rows.append({
                    "name_a": uniques[by_key[ki][0]],
                    "name_b": uniques[by_key[kj][0]],
                    "core_a": ki,          # keys are shown so you can see WHY
                    "core_b": kj,          # a pair scored the way it did
                    "score": round(float(s), 1),
                    "merge": "",           # <- you (or an LLM) fill in y/n
                })

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
                score(ks[a], ks[b])
                for a in range(len(ks))
                for b in range(a + 1, len(ks))
            )
        else:
            worst = 100.0

        for n in names:
            rows.append({
                "raw_name": n,
                "n_records": counts[n],
                "cluster_id": cid,
                "canonical_name": canonical,
                "cluster_size": len(names),
                "min_internal_score": round(float(worst), 1),
                "chain_risk": worst < auto_threshold,   # <- triage on this first
            })

    # Sorted biggest-cluster-first: the largest clusters carry the most risk and
    # are what you want to eyeball before trusting the run.
    crosswalk = pd.DataFrame(rows).sort_values(
        ["cluster_size", "cluster_id", "n_records"],
        ascending=[False, True, False],
    )
    review = (
        pd.DataFrame(review_rows).sort_values("score", ascending=False)
        if review_rows
        else pd.DataFrame(columns=["name_a", "name_b", "core_a", "core_b",
                                   "score", "merge"])
    )
    return crosswalk, review


# ==========================================================================
# 5. CLI
# ==========================================================================

def main() -> None:
    ap = argparse.ArgumentParser(
        description="Cluster messy coffee roaster names into canonical entities."
    )
    ap.add_argument("infile", type=Path, help="CSV containing the names")
    ap.add_argument("--column", default="roaster", help="column holding the names")
    ap.add_argument("--outdir", type=Path, default=Path("."))
    ap.add_argument("--auto", type=int, default=92,
                    help="score >= this: merge automatically (raise if you see "
                         "false merges)")
    ap.add_argument("--review", type=int, default=82,
                    help="score in [review, auto): send to human review queue "
                         "(lower it if true matches are being missed entirely)")
    args = ap.parse_args()

    df = pd.read_csv(args.infile)
    names = df[args.column].dropna().astype(str).tolist()

    crosswalk, review = resolve(names, args.auto, args.review)

    args.outdir.mkdir(parents=True, exist_ok=True)
    crosswalk.to_csv(args.outdir / "crosswalk.csv", index=False)
    review.to_csv(args.outdir / "review.csv", index=False)

    # These three numbers are your run diagnostics. Read them in order:
    #   merged        — did it do anything at all?
    #   review        — how much human work is left?
    #   chain-risk    — did single-linkage misbehave? THIS IS THE ONE THAT MATTERS.
    n_raw = crosswalk.raw_name.nunique()
    n_can = crosswalk.canonical_name.nunique()
    print(f"{n_raw} distinct spellings -> {n_can} roasters ({n_raw - n_can} merged)")
    print(f"{len(review)} pairs queued for review  -> review.csv")
    print(f"{int(crosswalk.chain_risk.sum())} rows in chain-risk clusters"
          f"{'  <-- INSPECT THESE' if crosswalk.chain_risk.any() else ''}")


if __name__ == "__main__":
    main()
