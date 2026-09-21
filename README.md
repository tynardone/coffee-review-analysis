# Coffee Review Scraper and Analysis

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-Ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

Scrapes the ~9,300 blind-tasting reviews published by
[CoffeeReview.com](https://www.coffeereview.com/) since 1997, cleans them into a
tabular dataset, and analyses price and quality trends. Prices are spread across
a dozen currencies and three decades, so comparing them takes historical
exchange rates and CPI; roaster names are spelled inconsistently, so comparing
roasters takes entity resolution.

- [Installation](#installation)
- [Data sources](#data-sources)
- [Code layout](#code-layout)
- [Usage](#usage)
- [Data layers](#data-layers)
- [Resolving roaster names](#resolving-roaster-names)
- [Notebooks](#notebooks)
- [Tests](#tests)
- [References](#references)

## Installation

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/tynardone/coffee-review-analysis.git
cd coffee-review-analysis
uv sync
```

`uv sync` creates `.venv`, installs the pinned dependencies from `uv.lock`, and
installs the `coffee` package in editable mode. It includes the `analysis` and
`dev` dependency groups by default; `uv sync --no-default-groups` gives a lean
environment with only the scraping and pipeline dependencies.

Fetching exchange rates needs an
[OpenExchangeRates](https://openexchangerates.org/signup/free) key (free tier:
1000 requests/month). It is the only credential the pipeline uses. Put it in the
environment or in a `.env` file at the project root:

```plaintext
OPENEXCHANGERATES_API_ID=
```

## Data sources

**CoffeeReview.com** — the reviews themselves, published since 1997. The raw
scrape needs substantial cleanup.

Review URLs come from `sitemap_index.xml` rather than from crawling the
paginated listings. The sitemap is a strict superset (9,333 review URLs against
the 9,054 pagination found, with none going the other way) and costs ~17
requests instead of hundreds. Each entry carries a `<lastmod>` date, recorded as
`sitemap_lastmod`, which is what lets a later run re-fetch only what changed.

**OpenExchangeRates** — historical daily rates, used to put every price in USD
at the rate for the month a review was published.

**US Consumer Price Index** (`data/external/consumer_price_index.csv`) — CPI-U,
US city average, all items, not seasonally adjusted. Fetched from the BLS public
API (series `CUUR0000SA0`), which needs no key. Used to express historical
prices in constant dollars.

**Geocoding** (planned) — a free API from [Map Maker](https://maps.co/), for
resolving roaster and origin locations to coordinates. Nothing calls it yet.

## Code layout

Everything importable lives in the `coffee/` package; the pipeline steps are
installed as console commands that wrap it.

**`coffee/`**

- `sitemap.py` — discovers every review URL from the site's XML sitemaps with
  each one's `<lastmod>`. Raises `SitemapError` rather than returning a partial
  list.
- `fetch.py` — shared async HTTP GET with bounded concurrency and retry, used by
  both discovery and scraping.
- `parser.py` — turns one review's HTML into structured fields.
- `review.py` — fetches a single review page and parses it into a record.
- `pipeline.py` — the full run: discovers every review URL and fetches only
  those that are new or have changed.
- `storage.py` — where reviews live. `CsvReviewStore` keeps
  `data/raw/reviews.{csv,json}`; the pipeline talks to the protocol, so a
  database backend can replace it without touching the scrape.
- `clean.py` — the cleaned layer: types, price/currency/quantity parsing,
  origin and roaster locations, the roaster crosswalk, and the price
  conversions from `enrich.py`.
- `enrich.py` — USD conversion and inflation adjustment, applied by
  `clean_reviews` when reference data is available. A separate module because
  it is the only part of cleaning that needs data the reviews do not carry.
  Every step in both modules is a pure `DataFrame -> DataFrame` function taking
  its reference data as an argument.
- `exchange_rates.py` — fetches historical rates for the review months in the
  raw scrape.
- `cpi.py` — fetches the BLS consumer price index. Merges into the stored table
  rather than replacing it, so a three-year API window cannot shrink a series
  going back to 1990.
- `roasters/` — entity resolution for messy roaster names, split along the
  cascade it runs: `normalize` reduces a name to a comparable key,
  `similarity` scores two keys, `location` supplies the second signal,
  `cluster` assembles the crosswalk, `decisions` holds the adjudicated pairs —
  the only module here that touches disk — and `report` formats what a run
  says about itself. See
  [Resolving roaster names](#resolving-roaster-names) for the workflow and
  [`docs/roaster-resolution.md`](docs/roaster-resolution.md) for the design.
- `config.py` — paths and credentials, read from the environment or `.env`.
- `cli.py` — argument parsing for the console commands.

**`tests/`** — `fixtures/html/` holds ten real review pages and
`fixtures/parsed_reviews.json` pins their expected parse; `generate_golden.py`
regenerates that file after a deliberate parser change.

**`docs/`** — [`roaster-resolution.md`](docs/roaster-resolution.md), the design
behind roaster entity resolution.

## Usage

Run from the repository root. `uv run` executes inside the project's virtual
environment without activating it.

Collection and cleaning are automated end to end:

```bash
uv run refresh-data
uv run jupyter lab
```

`refresh-data` runs the five steps in dependency order and prints what each did:

```bash
uv run scrape-reviews                            # 1. data/raw/reviews.csv
uv run resolve-roasters data/raw/reviews.csv     # 2. the roaster crosswalk
uv run fetch-exchange-rates                      # 3. rates for the review months
uv run fetch-cpi                                 # 4. the CPI table
uv run clean-reviews                             # 5. data/clean/reviews.csv
```

Each is also a command in its own right, and `--help` lists the options. Useful
flags on `refresh-data`:

- `--full` re-fetches and re-parses every review, not only what changed
- `--skip-scrape` rebuilds from reviews already held, making no review requests
- `--baseline-date` sets the month whose dollars adjusted prices use

Analysis stops there. The notebooks read `data/clean/reviews.csv`; the pipeline
does not produce charts or aggregates.

### Scraping is incremental

`data/raw/reviews.csv` is the single source of truth, updated in place. Git
holds the history, so the filename carries no date.

Discovery reads `sitemap_index.xml` in ~17 requests and returns every review URL
with its `<lastmod>`. A run fetches only URLs that are new, that changed since
the last run, or whose freshness cannot be proven — on the current corpus, a
handful of pages in about a second, against ~9,300 pages and half an hour for a
full pass.

Every fetched row records `scraped_at`, giving the age of any given row. Reviews
that disappear from the sitemap are reported and kept: they cannot be fetched
again, so the held copy is the only one.

```bash
uv run scrape-reviews --full
```

Use `--full` after changing `coffee/parser.py`. An incremental run re-parses
only the pages it re-fetches, so without it a parser fix reaches new rows only
and the corpus becomes a mixture of two parser versions. `scraped_at` is what
makes such a mixture visible.

### The CPI table

`fetch-cpi` reads series `CUUR0000SA0` from the BLS public API — CPI-U, US city
average, all items, not seasonally adjusted — and merges it into
`data/external/consumer_price_index.csv`, keeping the BLS's own wide layout.

No API key. The unkeyed v1 tier returns the last three years, which is all an
incremental update needs, and allows 25 requests a day. `--start-year` and
`--end-year` switch to the POST form for rebuilding a specific range, up to ten
years per request.

A published CPI figure for a past month does not change, so months already held
are never overwritten with anything and the response's narrow window cannot
shrink a table going back to 1990. Two details the series carries:

- period `M13` is the **annual average**, not a month, and is discarded
- a month BLS never published reads as `-` and is left blank. October 2025 is
  one: the index was not produced during that year's lapse in appropriations.
  `cpi_adjust_price` leaves such a month's prices unadjusted rather than
  dropping them.

### Exchange rates are incremental too

`fetch-exchange-rates` reads the review months from the raw scrape and requests
only the ones missing from `data/external/openex_exchange_rates.json`. Reading
from the raw layer keeps the pipeline acyclic: cleaning consumes these rates, so
it cannot also be what produces the list of months to fetch.

Rates for a past date do not change, so a month once held is never requested
again. Re-running against a current file costs zero requests, which matters
against 1000 per month and a corpus spanning 323 distinct months.

Two properties protect the stored file, which is the only copy of that data:

- a failed request is never written, so a rate-limited run cannot replace
  populated months with blanks
- results are checkpointed during the run, so an interrupted one keeps the
  requests it already spent

A month whose request failed is left unheld and retried next run. `--refetch`
re-requests everything and exists only for repairing a corrupt file.

## Data layers

```
data/raw/reviews.csv     RAW      as scraped, never edited
data/clean/reviews.csv   CLEANED  typed, parsed, roasters resolved
```

**Field names are settled at the raw boundary.** `coffee/parser.py` normalises
each scraped table label (`"Est. Price:"` → `est_price`) as it parses, so the
raw layer lands with the names the rest of the project uses. Cleaning therefore
concerns data only: `clean_reviews` asserts the names are already correct rather
than fixing them, so a file predating this fails at the boundary naming the
offending columns instead of failing inside a merge several steps later.

`uv run clean-reviews` builds the cleaned layer. The transformation lives in
`coffee/clean.py` rather than in a notebook, so it is tested and runs in CI.
Cleaning:

- parses `est_price` into a value, an ISO 4217 currency and a quantity
- converts quantities to pounds, so prices are comparable per unit
- resolves origin and roaster locations to countries, and US states
- adds `roaster_canonical` from the crosswalk, keeping the raw spelling beside it

Rows whose agtron reading exceeds 100 are dropped as site typos, and the count
is reported on every run. Formats that are not whole-bean coffee (capsules,
pods) keep their review but get no quantity, since a price per pound would not
mean anything.

**Prices are made comparable as part of cleaning**, by `coffee/enrich.py`.
`clean_reviews` applies it when given exchange rates and CPI, adding:

- `price_usd`, converted at the review month's rate
- `price_usd_adj`, in a baseline month's dollars, so a 1997 price and a 2026
  price can be compared
- `price_usd_adj_per_lb`

The figures the field-level steps recorded — `price_value`, `price_currency`,
`quantity_in_lbs` — are left untouched beside them.

`--baseline-date` chooses the month (default `2026-01-01`). It must be a month
the CPI table covers; a baseline outside that range is refused rather than
silently adjusted, since a different baseline changes every price. `fetch-cpi`
keeps the table current.

Both reference files are optional. Without them `clean-reviews` still produces
the field-level layer and warns that prices stay in their original currency,
which is what lets it run on a fresh checkout.

## Resolving roaster names

The same roaster is spelled many ways: `Onyx Coffee Lab` / `Onyx Coffee Lab
LLC` / `onyx coffee lab`. `resolve-roasters` groups the spellings and records
which pairs have been judged, so the manual work shrinks with each run rather
than starting over.

Only the name and location signals decide automatically; the uncertain middle
goes to a review queue. [`docs/roaster-resolution.md`](docs/roaster-resolution.md)
explains why.

### The three files

They live in `data/roasters/`.

| file | edit it? | role |
|---|---|---|
| `roaster_decisions.csv` | **yes** | Source of truth: pairs that have been adjudicated. The only one that cannot be regenerated. Commit it. |
| `roaster_review_queue.csv` | **yes** — the `verdict` column only | Pairs the tool could not decide. Regenerated every run. |
| `roaster_crosswalk.csv` | **no** | The output: `raw_name → canonical_name`. Regenerated every run; hand edits are overwritten. Commit it so downstream joins are reproducible. |

### The loop

**1. Resolve.**

```bash
uv run resolve-roasters data/raw/reviews.csv
```

It prints four lines, which answer four questions:

```
1687 distinct spellings -> 1496 roasters (191 merged)      did it do anything?
18 decisions applied from …/roaster_decisions.csv          are past calls applied?
0 pairs queued for review -> …/roaster_review_queue.csv    how much is left to judge?
11 rows in chain-risk clusters  <-- INSPECT THESE          did clustering misbehave?
```

**2. Judge the queue.**

Open `data/roasters/roaster_review_queue.csv` and answer in the `verdict`
column. That column is the only thing to change.

`merge` / `y` / `yes` / `m` / `same` / `1` all mean **same company**;
`split` / `n` / `no` / `s` / `different` / `0` all mean **different**. Anything
else is reported as an error rather than skipped, so a typo cannot silently
discard a row. Blank rows come back next time.

| name_a | name_b | score | location_evidence | verdict |
|---|---|---|---|---|
| Boyd Coffee | Boyds Coffee | 88.9 | `same` | `merge` |
| Fellow Coffee | Mellow Coffee | 83.3 | `neutral` | `split` |

`location_evidence` is the shortcut:

- **`same`** — one address. Usually the same company, but not always: `Wei Chuan
  Foods` and `Tehmag Foods` share a city and are unrelated. Read the names.
- **`neutral`** — same region, different city. Usually different companies.
- **`unknown`** — no location on one side. Judge on the names alone.

**3. Record the verdicts and re-resolve.**

```bash
uv run resolve-roasters data/raw/reviews.csv --accept-reviewed --decided-by "$USER"
```

This folds the answers into `roaster_decisions.csv`, then re-resolves with them
applied. The queue returns holding only the rows left blank.

Step 1 regenerates the queue, so a run without `--accept-reviewed` would
overwrite unrecorded answers. The command refuses to do that and names the flag
to use, but filling in the queue should always be followed by this step.

**4. Commit all three files.** `roaster_decisions.csv` matters most, being the
only one that cannot be rebuilt.

### On the next scrape

Run step 1 against the new file. The queue holds only pairs not yet judged;
everything already decided stays decided. An answered pair reappearing means
`roaster_decisions.csv` is missing from `--outdir`.

### Occasional extras

Inspect the chain-risk clusters, which single-linkage could only have assembled
transitively and are therefore the likeliest false merges:

```bash
uv run python -c "import pandas as pd; c=pd.read_csv('data/roasters/roaster_crosswalk.csv'); print(c[c.chain_risk][['raw_name','canonical_name','min_internal_score']].to_string(index=False))"
```

Surface merges the name score alone misses — pairs below the normal floor that
share an address, which is how `Starbucks` ~ `Starbucks Reserve Roastery`
(score 72) turns up. They are queued for judgement, never merged. On the current
data this adds 13 pairs to an otherwise empty queue:

```bash
uv run resolve-roasters data/raw/reviews.csv --location-review 70
```

### When a split doesn't stick

A pair can be split and still end up together. Single-linkage can rejoin two
names through a third name resembling both, most often a collaboration:

```
RND                               -> key 'rnd'
Red Rooster Coffee Roaster        -> key 'red rooster'
RND & Red Rooster Coffee Roaster  -> key 'red rnd rooster'   superset of both
```

Blocking the direct union does not help, since the two rejoin through the
collaboration. The run reports it:

```
!! 1 cluster(s) VIOLATE a split decision -- these names were kept together
   despite your verdict:
    RND  |  RND & Red Rooster Coffee Roaster  |  Red Rooster Coffee Roaster
```

The fix is to record a split against the bridging name too: here, `RND` against
`RND & Red Rooster Coffee Roaster`. Rows in an affected cluster are also marked
`violates_decision` in the crosswalk.

### Rules

1. **Never hand-edit `roaster_crosswalk.csv`.** It is regenerated on every run.
   To change a grouping, change the decision that produced it.
2. **To reverse a call, edit `roaster_decisions.csv` directly.**
   `--accept-reviewed` will not overwrite an existing verdict, so a change of
   mind shows up as a visible diff rather than happening silently.

## Notebooks

Run them in order; each depends on the previous one's output.

| notebook | reads | writes |
|---|---|---|
| `01-data-cleaning` | `data/raw/reviews.csv` | `data/clean/reviews.csv` (the same work `clean-reviews` does) |
| `02-data-EDA` | `data/clean/reviews.csv` | charts |
| `03-text-features` | `data/clean/reviews.csv` | wordclouds in `imgs/` |

`data/clean/reviews.csv` is gitignored; notebook 01 regenerates it, so run that
first on a fresh checkout. Committed data is limited to the scrape itself and to
outputs carrying human judgement, namely the roaster crosswalk and decisions.

Notebook 03 needs two downloads that are not Python packages. It fetches the
NLTK corpora itself; the spaCy model is installed once:

```bash
uv run python -m spacy download en_core_web_sm
```

Notebook outputs are cleared before committing, since they reached 13MB of
embedded images against an already-large history. The figures worth keeping go
to `imgs/`.

## Tests

```bash
uv run pytest
```

`tests/fixtures/html/` holds ten real review pages and
`tests/fixtures/parsed_reviews.json` pins their expected parse, so a parser
regression fails a test rather than emptying a column unnoticed. After a
deliberate parser change, regenerate the golden file and read the diff:

```bash
uv run python tests/generate_golden.py
```

Linting (ruff), formatting (ruff-format) and type checking (mypy) run through
pre-commit. Install the hooks once after cloning:

```bash
uv run pre-commit install
```

GitHub Actions runs the same checks on every pull request, plus the tests on
Python 3.12 and 3.13. The tests make no network requests.

## References

- [OpenExchangeRates API](https://docs.openexchangerates.org/reference/api-introduction)
- [BLS Consumer Price Index data](https://www.bls.gov/cpi/data.htm)
- [How to Use t-SNE Effectively](https://distill.pub/2016/misread-tsne/) — relevant to the embedding work in `03-text-features.ipynb`
- `notes/how_coffee_review_works.md` — how CoffeeReview scores coffees
