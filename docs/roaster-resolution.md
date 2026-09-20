# Roaster name resolution: the design

Why [`coffee/roaster_resolution.py`](../coffee/roaster_resolution.py) is built the
way it is. For the *workflow* — which commands to run, in what order, and what to
do with the review queue — see [Resolving roaster names](../README.md#resolving-roaster-names)
in the README.

## The problem

Scraped review data spells the same roaster several ways:

```
"Onyx Coffee Lab" / "Onyx Coffee Lab LLC" / "onyx coffee lab" / "Onyx Coffee"
```

Group them, pick one canonical spelling, and persist the mapping.

## The governing asymmetry

The two error types are **not** equally costly, and every decision in the module
is bought with that fact.

| Error | Example | Cost |
| --- | --- | --- |
| **False merge** | Black Oak + Black & White → one roaster | **Silent.** Downstream analysis runs fine. Scores for "Black Oak" are now a blend of two companies and you will never notice. |
| **False split** | "Stumptown" and "Stumptown Coffee" stay separate | **Loud.** Stumptown shows up twice in your top-20 table. You catch it instantly. |

So: optimise for **precision, not recall**. Leave merges on the table rather than
make a wrong one. Recall failures announce themselves; precision failures don't.

This is also why OpenRefine's UI is risky here — approving a cluster is one
click, and all the friction sits on the *reject* side, exactly backwards from
where the risk lives.

## The cascade

Cheapest and most certain first; expensive and most doubtful last. Each stage
shrinks the input to the next, so by the time we reach the part that can be
wrong, there is very little left for it to be wrong about.

```
normalize  ->  exact key  ->  location veto  ->  fuzzy  ->  human/LLM review
  free         ~100%          near-decisive     O(n^2)      expensive
```

### Normalization does most of the work

Under plain edit distance, `"Stumptown"` vs `"Stumptown Coffee Roasters"` is 15
edits on a 24-character string — you would need a threshold so loose it would
merge half the dataset.

Strip the domain stopwords and **both become the string `"stumptown"`**. Not
similar — *identical*. They now collide on an exact hash lookup, which has ~100%
precision by construction: no threshold to tune, no judgment to get wrong. A
large fraction of the problem is solved for free, before any scoring happens.

`STOPWORDS` is therefore the first thing to edit for new data. Misspellings of
the stopwords themselves belong in it too (`"coffe"`, `"cofee"`); they would
otherwise survive into the key as noise tokens.

## The second signal: location

Names alone leave a wide uncertainty band. Roaster location is populated on
essentially every review and is nearly orthogonal to spelling, so it closes most
of that band for free: on the real data it resolved 41 of 50 queued pairs and cut
the queue from 50 to 13.

Its two directions are **not symmetric**, and that asymmetry is load-bearing:

- **Different region → veto the merge.** Near-decisive, and it fires in the safe
  direction — refusing a merge can only cause a loud false split.
- **Identical place → surface the pair for review; never merge it.**

Merging on a lowered bar was tried and is unsafe, because the string score does
not separate the good cases from the bad:

| Pair | Score | Truth |
| --- | --- | --- |
| `Great Value (Walmart)` / `Great Value (Wal-Mart)` | 82.1 | same company |
| `Tehmag Foods` / `Wei Chuan Foods` | 82.9 | **unrelated** — two Taiwanese companies sharing a city |

No threshold separates them. Auto-merging there would buy recall with exactly the
silent false merges this module exists to prevent. Surfacing costs one answer
instead, and answers persist.

## Why two thresholds, not one

A single threshold forces a lie — it asserts every pair is either a match or not,
with the boundary at a number you made up. But there is a real middle region
where **the strings simply do not contain the answer**:

```
"Black Oak Coffee Roasters" vs "Black & White Coffee Roasters"  ->  71
"Red Bay Coffee"            vs "Red Rooster Coffee Roaster"     ->  60
```

These are not matches, but nothing in the strings says so. The only thing that
resolves them is knowing these are four different companies. That is world
knowledge, not string knowledge. So:

```
score >= auto_threshold     merge automatically
review..auto                honest uncertainty -> review queue
score <  review_threshold   leave alone
```

The review band is exactly where an LLM earns its keep — and nowhere else.
Handing a model 500 raw names and asking it to canonicalise is asking it to
hallucinate at scale with no way to audit the result. Handing it 30 pre-scored
ambiguous pairs and asking "same company, y/n, why?" is a bounded, verifiable
task on precisely the cases where world knowledge beats the string metric — and
its errors land somewhere you are already looking.

### Tuning the thresholds

The softest part of the whole design. `92` / `82` are tuned on a toy set. To get
real numbers for your data: run it, sort the review queue by score descending,
and find where **true** matches stop appearing. That score is your real `auto`.
Then find where *plausible* matches stop appearing entirely — that is your real
`review` floor.

## State: what is derived and what is not

| File | Status |
| --- | --- |
| `roaster_decisions.csv` | **Source of truth.** Adjudicated pairs, with who decided and when. Hand-edited, committed to git. The only file here that cannot be regenerated. |
| `roaster_crosswalk.csv` | **Derived.** `raw_name` → `canonical_name`. Regenerated every run; never hand-edit it, your edit would be overwritten. Commit it so downstream joins are reproducible. |
| `roaster_review_queue.csv` | **Derived.** Only pairs with no decision yet, with a blank `verdict` column to fill in, and each side's location so the evidence is visible without a second lookup. |

Separating the first from the other two is what makes manual effort
**accumulate**. Re-running re-derives the clusters but never re-asks a question
already answered, so the queue shrinks toward zero instead of resetting to full
every time — which is what the OpenRefine workflow does.

## Known limits

- **Single-linkage chaining.** Clusters are connected components, so A and C can
  fuse through B even if they would score 40 against each other — the classic
  failure mode, and exactly what OpenRefine's nearest-neighbour clustering does
  too. Not prevented; *reported*, via the `chain_risk` column (the worst
  pairwise score inside each cluster). Complete-linkage clustering is stricter
  but not *correct* — it trades one error set for another — whereas chaining is
  detectable, and a cheap algorithm plus a loud alarm beats an expensive one
  with none. If `chain_risk` lights up frequently on real data, *then* swap in
  complete linkage (`scipy.cluster.hierarchy`, `method="complete"`). Not
  before.
- **Split decisions can be defeated transitively.** Blocking a union is not the
  same as keeping two names apart: `RND` and `Red Rooster Coffee Roaster` are
  bridged by the collaboration `RND & Red Rooster Coffee Roaster`, whose key is a
  superset of both. Reported via `violates_decision`. Fixed by splitting the
  bridging name too.
- **Canonical selection assumes the most common spelling is correct.** Usually
  true in scraped data, not always. When it picks something ugly, don't fight the
  heuristic — add a `canonical_overrides.csv` and apply it afterward.
- **A roaster genuinely named "The Coffee Company"** stopwords down to nothing
  and falls back to the full fingerprint. A patch, not a solution: such a name
  also matches poorly against its own variants.
