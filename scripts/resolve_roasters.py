"""
resolve_roasters.py — entity resolution for messy coffee roaster names.

Pipeline:
    normalize -> exact-key collision -> fuzzy scoring -> graph clustering
    -> canonical name selection -> crosswalk + review queue

Outputs two CSVs:
    crosswalk.csv  — raw_name -> canonical_name (the durable artifact; commit this)
    review.csv     — borderline pairs a human should adjudicate

Usage:
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

# --------------------------------------------------------------------------
# 1. NORMALIZATION
# --------------------------------------------------------------------------
# These carry almost no identifying information for a coffee roaster. Removing
# them before scoring is the highest-leverage step in the whole pipeline.
STOPWORDS = {
    "coffee", "coffees", "coffe", "cofee",
    "roaster", "roasters", "roasting", "roastery", "roasterie",
    "cafe", "caffe", "kaffee", "espresso", "bean", "beans",
    "co", "company", "inc", "incorporated", "llc", "ltd", "limited", "corp",
    "the", "and",
}

# Expanded *before* stopword removal so "Bros." and "Brothers" agree.
ABBREV = {
    "bros": "brothers",
    "bro": "brothers",
    "mfg": "manufacturing",
    "intl": "international",
    "st": "saint",
    "mt": "mount",
}


def strip_accents(s: str) -> str:
    """Café -> Cafe"""
    return "".join(
        c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)
    )


def tokens(name: str) -> list[str]:
    s = strip_accents(str(name)).lower()
    s = s.replace("&", " and ")
    s = re.sub(r"['\u2019]", "", s)              # Willoughby's -> willoughbys
    s = re.sub(r"[^a-z0-9\s]", " ", s)           # drop remaining punctuation
    s = re.sub(r"\s+", " ", s).strip()
    toks = [ABBREV.get(t, t) for t in s.split()]

    # Collapse runs of single letters: "j b c" -> "jbc", so that
    # "J.B.C. Coffee Roasters" and "JBC Coffee" agree.
    out, run = [], []
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
    """OpenRefine's key-collision fingerprint, reimplemented."""
    toks = sorted(set(tokens(name)))
    return " ".join(toks)


def core_key(name: str) -> str:
    """Fingerprint with domain stopwords removed. This is the real workhorse."""
    toks = sorted({t for t in tokens(name) if t not in STOPWORDS})
    if not toks:                                 # e.g. name was literally "Coffee Co"
        toks = sorted(set(tokens(name)))
    return " ".join(toks)


# --------------------------------------------------------------------------
# 2. SIMILARITY
# --------------------------------------------------------------------------
def score(a: str, b: str, **kwargs) -> float:
    """Blend of two views. token_set handles subset names ('Stumptown' vs
    'Stumptown Coffee Roasters'); token_sort handles reordering and typos.
    **kwargs absorbs the score_cutoff rapidfuzz passes to scorers."""
    return max(
        fuzz.token_set_ratio(a, b),
        fuzz.token_sort_ratio(a, b),
    )


# --------------------------------------------------------------------------
# 3. UNION-FIND
# --------------------------------------------------------------------------
class DSU:
    def __init__(self, n: int):
        self.p = list(range(n))

    def find(self, x: int) -> int:
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


# --------------------------------------------------------------------------
# 4. MAIN
# --------------------------------------------------------------------------
def resolve(
    raw_names: list[str],
    auto_threshold: int = 92,
    review_threshold: int = 82,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    counts = Counter(raw_names)
    uniques = sorted(counts)

    keys = [core_key(n) for n in uniques]

    # -- Stage A: exact core-key collision (free, high precision) ------------
    dsu = DSU(len(uniques))
    by_key: dict[str, list[int]] = defaultdict(list)
    for i, k in enumerate(keys):
        by_key[k].append(i)
    for group in by_key.values():
        for j in group[1:]:
            dsu.union(group[0], j)

    # -- Stage B: fuzzy scoring on the deduped core keys ---------------------
    distinct_keys = sorted(by_key)
    key_to_idx = {k: i for i, k in enumerate(distinct_keys)}

    matrix = process.cdist(
        distinct_keys, distinct_keys,
        scorer=score,
        score_cutoff=review_threshold,
        workers=-1,
    )

    review_rows = []
    for i in range(len(distinct_keys)):
        for j in range(i + 1, len(distinct_keys)):
            s = matrix[i][j]
            if s < review_threshold:   # cdist's score_cutoff is advisory for
                continue               # custom scorers, so filter explicitly
            ki, kj = distinct_keys[i], distinct_keys[j]
            if s >= auto_threshold:
                dsu.union(by_key[ki][0], by_key[kj][0])
            else:
                review_rows.append({
                    "name_a": uniques[by_key[ki][0]],
                    "name_b": uniques[by_key[kj][0]],
                    "core_a": ki,
                    "core_b": kj,
                    "score": round(float(s), 1),
                    "merge": "",          # <- you fill in y/n
                })

    # -- Stage C: assemble clusters -----------------------------------------
    clusters: dict[int, list[int]] = defaultdict(list)
    for i in range(len(uniques)):
        clusters[dsu.find(i)].append(i)

    rows = []
    for cid, (root, members) in enumerate(sorted(clusters.items())):
        names = [uniques[m] for m in members]
        # canonical = the spelling that appears most often in the source data,
        # tie-broken by longest (more complete) form.
        canonical = max(names, key=lambda n: (counts[n], len(n)))

        # chain detection: union-find is single-linkage, so A~B~C can merge
        # A and C even if they're unrelated. Flag clusters whose worst
        # internal pair is weak.
        if len(names) > 2:
            ks = [core_key(n) for n in names]
            worst = min(
                score(ks[a], ks[b])
                for a in range(len(ks)) for b in range(a + 1, len(ks))
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
                "chain_risk": worst < auto_threshold,
            })

    crosswalk = pd.DataFrame(rows).sort_values(
        ["cluster_size", "cluster_id", "n_records"], ascending=[False, True, False]
    )
    review = (
        pd.DataFrame(review_rows).sort_values("score", ascending=False)
        if review_rows else pd.DataFrame(columns=["name_a", "name_b", "score", "merge"])
    )
    return crosswalk, review


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("infile", type=Path)
    ap.add_argument("--column", default="roaster")
    ap.add_argument("--outdir", type=Path, default=Path("."))
    ap.add_argument("--auto", type=int, default=92,
                    help="score >= this: merge automatically")
    ap.add_argument("--review", type=int, default=82,
                    help="score in [review, auto): send to human review queue")
    args = ap.parse_args()

    df = pd.read_csv(args.infile)
    names = df[args.column].dropna().astype(str).tolist()

    crosswalk, review = resolve(names, args.auto, args.review)

    args.outdir.mkdir(parents=True, exist_ok=True)
    crosswalk.to_csv(args.outdir / "crosswalk.csv", index=False)
    review.to_csv(args.outdir / "review.csv", index=False)

    n_raw = crosswalk.raw_name.nunique()
    n_can = crosswalk.canonical_name.nunique()
    print(f"{n_raw} distinct spellings -> {n_can} roasters "
          f"({n_raw - n_can} merged)")
    print(f"{len(review)} pairs queued for review")
    print(f"{int(crosswalk.chain_risk.sum())} rows in chain-risk clusters")


if __name__ == "__main__":
    main()
