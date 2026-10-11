# Roaster name resolution

[← README](../README.md) · [All documentation](README.md)

How to group roaster spellings with `resolve-roasters`, and the design behind
[`src/coffee/roasters/`](../src/coffee/roasters/).

## Using the resolver

### Files

| File | Location | Edit? | Purpose |
|---|---|---|---|
| `roaster_decisions.csv` | `curated/` | Yes | Every pair that has been judged. The only file that cannot be regenerated. |
| `review_queue.csv` | `data/roasters/` | `verdict` column only | Pairs awaiting judgment. Regenerated on every run. |
| `crosswalk.csv` | `data/roasters/` | No | The output, mapping `raw_name` to `canonical_name`. Regenerated on every run. |

All three are committed. The crosswalk is committed so that joins against it are
reproducible.

### Steps

**1. Resolve.**

```bash
uv run resolve-roasters data/parsed/reviews.csv
```

The summary answers four questions:

```
1688 distinct spellings -> 1497 roasters (191 merged)    did it do anything?
18 decisions applied from …/roaster_decisions.csv        are past calls applied?
0 pairs queued for review -> …/review_queue.csv          how much is left to judge?
11 rows in chain-risk clusters  <-- INSPECT THESE        did clustering misbehave?
```

**2. Judge the queue.** Open `data/roasters/review_queue.csv` and fill in
the `verdict` column. Leave every other column alone.

- Same company: `merge`, `yes`, `y`, `m`, `same` or `1`
- Different companies: `split`, `no`, `n`, `s`, `different` or `0`

Rows left blank come back on the next run. Any other value is reported as an
error.

| name_a | name_b | score | location_evidence | verdict |
|---|---|---|---|---|
| Boyd Coffee | Boyds Coffee | 88.9 | `same` | `merge` |
| Fellow Coffee | Mellow Coffee | 83.3 | `neutral` | `split` |

The `location_evidence` column is a useful guide:

- `same`: both names share an address. Usually one company, but not always;
  `Wei Chuan Foods` and `Tehmag Foods` share a city and are unrelated.
- `neutral`: same region, different city. Usually different companies.
- `unknown`: at least one name has no location. Judge on the names alone.

**3. Record the verdicts and resolve again.**

```bash
uv run resolve-roasters data/parsed/reviews.csv --accept-reviewed --decided-by "$USER"
```

This adds your answers to `curated/roaster_decisions.csv` and resolves again with
them applied. The new queue holds only the rows you left blank.

Always follow step 2 with this step. A plain run regenerates the queue, which
would discard answers not yet recorded, so the command refuses to run while the
queue holds any and tells you to add `--accept-reviewed`.

**4. Commit the three files.**

After the next scrape and parse, start again at step 1. Pairs already decided stay
decided, so the queue holds only new questions. If an answered pair reappears,
check that `curated/roaster_decisions.csv` exists and that `--decisions` is not
pointing elsewhere.

### Checking the results

Chain-risk clusters were joined only through a chain of similar names, which
makes them the likeliest false merges. To list them:

```bash
uv run python -c "import pandas as pd; c=pd.read_csv('data/roasters/crosswalk.csv'); print(c[c.chain_risk][['raw_name','canonical_name','min_internal_score']].to_string(index=False))"
```

To find matches the name score misses, also queue lower-scoring pairs that share
an address. This is how `Starbucks` and `Starbucks Reserve Roastery` (score 72)
come to light. These pairs are only queued, never merged automatically. On the
current data this adds 13 pairs:

```bash
uv run resolve-roasters data/parsed/reviews.csv --location-review 70
```

### When a split does not hold

Clustering links names in chains, so two names you have split can still end up
together through a third name that resembles both. The usual culprit is a
collaboration:

```
RND                               -> key 'rnd'
Red Rooster Coffee Roaster        -> key 'red rooster'
RND & Red Rooster Coffee Roaster  -> key 'red rnd rooster'   superset of both
```

Blocking the direct link does not help, because the two names remain connected
through the collaboration. The run warns when this happens:

```
!! 1 cluster(s) VIOLATE a split decision -- these names were kept together
   despite your verdict:
    RND  |  RND & Red Rooster Coffee Roaster  |  Red Rooster Coffee Roaster
```

To fix it, also record a split against the bridging name: here, `RND` against
`RND & Red Rooster Coffee Roaster`. Affected rows are flagged in the crosswalk's
`violates_decision` column.

### Rules

1. Never edit `data/roasters/crosswalk.csv` by hand; it is overwritten on every run. To
   change a grouping, change the decision behind it.
2. To reverse a decision, edit `curated/roaster_decisions.csv` directly.
   `--accept-reviewed` never overwrites an existing verdict, so a change of mind
   always shows up as a diff.

## The problem

Scraped review data spells the same roaster several ways:

```
"Onyx Coffee Lab" / "Onyx Coffee Lab LLC" / "onyx coffee lab" / "Onyx Coffee"
```

Group them, pick one canonical spelling, and persist the mapping.

## The governing asymmetry

The two error types are not equally costly, and the module's design follows from
that.

| Error | Example | Cost |
| --- | --- | --- |
| **False merge** | Black Oak + Black & White → one roaster | Silent. Downstream analysis still runs, but scores for "Black Oak" are now a blend of two companies. |
| **False split** | "Stumptown" and "Stumptown Coffee" stay separate | Visible. Stumptown appears twice in a top-20 table. |

Precision is therefore favored over recall: an uncertain merge is left unmade.
Recall failures surface on their own; precision failures do not.

The same asymmetry makes OpenRefine's interface a poor fit here. Approving a
cluster is a single click, so the friction falls on the reject side — the
opposite of where the risk is.

## The cascade

Cheapest and most certain first; expensive and most doubtful last. Each stage
shrinks the input to the next, so by the time we reach the part that can be
wrong, there is very little left for it to be wrong about.

```
normalize  ->  exact key  ->  location veto  ->  fuzzy  ->  human/LLM review
  free         ~100%          near-decisive     O(n^2)      expensive
```

### Normalization does most of the work

Under plain edit distance, `"Stumptown"` and `"Stumptown Coffee Roasters"` are
15 edits apart on a 24-character string, which would need a threshold loose
enough to merge much of the dataset.

Removing the domain stopwords reduces both to the string `"stumptown"`. They
then collide on an exact lookup, with no threshold to tune. A large share of the
problem is resolved before any scoring happens.

`STOPWORDS` is therefore the first thing to adjust for a new corpus.
Misspellings of the stopwords belong in it too (`"coffe"`, `"cofee"`), since
they would otherwise survive into the key as noise tokens.

## The second signal: location

Names alone leave a wide uncertainty band. Roaster location is populated on
nearly every review and is close to orthogonal to spelling, so it narrows that
band considerably: on the current corpus it settled 41 of 50 queued pairs,
reducing the queue from 50 to 13.

The two directions are not symmetric:

- **Different region → veto the merge.** Near-decisive, and it fires in the safe
  direction — refusing a merge can only cause a loud false split.
- **Identical place → surface the pair for review; never merge it.**

Merging on a lowered bar is unsafe, because the string score does not separate
the correct cases from the incorrect ones:

| Pair | Score | Truth |
| --- | --- | --- |
| `Great Value (Walmart)` / `Great Value (Wal-Mart)` | 82.1 | same company |
| `Tehmag Foods` / `Wei Chuan Foods` | 82.9 | **unrelated** — two Taiwanese companies sharing a city |

No threshold separates them, so auto-merging in this band would trade recall for
the silent false merges the module exists to avoid. Surfacing the pair instead
costs a single answer, which is then recorded permanently.

## Why two thresholds, not one

A single threshold asserts that every pair is either a match or not, with the
boundary at an arbitrary number. There is a middle region in which the strings
do not contain the answer:

```
"Black Oak Coffee Roasters" vs "Black & White Coffee Roasters"  ->  71
"Red Bay Coffee"            vs "Red Rooster Coffee Roaster"     ->  60
```

These are not matches, but nothing in the strings indicates that. Resolving them
requires knowing that these are four different companies, which is information
outside the strings. Hence:

```
score >= auto_threshold     merge automatically
review..auto                honest uncertainty -> review queue
score <  review_threshold   leave alone
```

The review band is the only part of this problem where a language model is
worth applying. Asking a model to canonicalize 500 raw names produces output
that cannot be audited. Asking it to answer "same company, y/n, why?" for 30
pre-scored ambiguous pairs is bounded and checkable, and it is exactly the set
of cases where outside knowledge beats the string metric.

### Tuning the thresholds

`92` and `82` are tuned on a small sample and are the least settled part of the
design. To derive them for a given corpus: run the resolver, sort the review
queue by score descending, and find where true matches stop appearing — that
score is the `auto` threshold. The point where plausible matches stop appearing
entirely is the `review` floor.

## State: what is derived and what is not

| File | Status |
| --- | --- |
| `curated/roaster_decisions.csv` | Source of truth. Adjudicated pairs, with who decided and when. Hand-edited and committed to git, in `curated/` rather than `data/roasters/` beside the other two. The only file here that cannot be regenerated. |
| `data/roasters/crosswalk.csv` | Derived. `raw_name` → `canonical_name`, regenerated on every run, so hand edits are overwritten. Committed so that downstream joins are reproducible. |
| `data/roasters/review_queue.csv` | Derived. Pairs with no decision yet, with a blank `verdict` column and each side's location, so that adjudicating a pair needs no second lookup. |

Separating the first from the other two is what lets manual effort accumulate.
Re-running re-derives the clusters but never re-asks an answered question, so the
queue shrinks toward zero rather than resetting each time.

## Known limits

- **Single-linkage chaining.** Clusters are connected components, so A and C can
  fuse through B even if they would score 40 against each other — the classic
  failure mode, and exactly what OpenRefine's nearest-neighbour clustering does
  too. Not prevented but reported, via the `chain_risk` column, which carries
  the worst pairwise score inside each cluster. Complete-linkage clustering is
  stricter but trades one error set for another, whereas chaining is detectable.
  If `chain_risk` fires frequently on a real corpus, complete linkage
  (`scipy.cluster.hierarchy`, `method="complete"`) is the alternative.
- **Split decisions can be defeated transitively.** Blocking a union is not the
  same as keeping two names apart: `RND` and `Red Rooster Coffee Roaster` are
  bridged by the collaboration `RND & Red Rooster Coffee Roaster`, whose key is a
  superset of both. Reported via `violates_decision`, and resolved by recording a
  split against the bridging name as well.
- **Canonical selection assumes the most common spelling is correct.** This
  holds for most scraped data but not all. Where it selects a poor form, a
  `canonical_overrides.csv` applied afterwards is the escape hatch.
- **A name composed entirely of stopwords**, such as "The Coffee Company",
  reduces to nothing and falls back to the full fingerprint. Such a name also
  matches poorly against its own variants.
