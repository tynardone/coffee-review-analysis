
# Coffee Review Scraper and Analysis

[![Python Version](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-Ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

This project is a complete data pipeline for scraping coffee reviews from [CoffeeReview.com](https://www.coffeereview.com/), followed by data cleaning, transformation, and analysis. The data is augmented with additional external datasets (e.g., consumer price index, exchange rates) and analyzed to explore trends in coffee quality.

## Table of Contents

- [Project Overview](#project-overview)
- [Installation](#installation)
- [Data Sources](#data-sources)
- [Code Layout](#code-layout)
- [Usage](#usage)
- [Resolving roaster names](#resolving-roaster-names)
- [The two layers](#the-two-layers)
- [Notebooks](#notebooks)
- [Tests](#tests)
- [References](#references)

## Project Overview

The project involves:

1. **Web Scraping**: Using Python to scrape coffee reviews and associated metadata and save to CSV.
2. **Data Cleaning**: Processing the scraped data, handling missing values, extensive cleaning and normalization, and augmenting with additional information.
3. **Analysis**: Generating visualizations and insights into coffee characteristics, quality scores, and tasting notes.

The goal is to provide insights into the boutique coffee market, with a focus on origin, price, flavor notes and quality metrics.

## Installation

### Prerequisites

- Python 3.12 (the project targets `>=3.12,<3.14`)
- [uv](https://docs.astral.sh/uv/) for dependency and environment management
- Git (optional)

### Setup

1. **Clone this repository**:

    ```bash
    git clone https://github.com/tynardone/coffee-review-analysis.git
    cd coffee-review-analysis
    ```

2. **Install dependencies**:

    `uv sync` creates a virtual environment in `.venv`, installs the pinned
    dependencies from `uv.lock`, and installs the `coffee` package itself in
    editable mode:

    ```bash
    uv sync
    ```

    This installs the `analysis` and `dev` dependency groups by default. For a
    lean environment with just the scraping/pipeline dependencies, run
    `uv sync --no-default-groups`.

3. **Obtain a free API key**:

    Fetching exchange rates requires an
    [OpenExchangeRates](https://openexchangerates.org/signup/free) key (free
    tier: 1000 requests/month). Add it to your environment or a `.env` file at
    the project root:

    ```plaintext
    OPENEXCHANGERATES_API_ID =
    ```

    That is the only key the pipeline needs.

## Data Sources

- **CoffeeReview.com**

    Source of the dataset of coffee roast reviews and target of webscraper.
    Operating since 1997 and amassing 1000s of blind-taste reviews of coffee roasts from around the world.
    The raw scraped data requires significant cleanup.

    Review URLs are discovered from `sitemap_index.xml` rather than by crawling
    the paginated listings. The sitemap is a strict superset — 9,333 review URLs
    against the 9,054 that pagination found, with none going the other way — and
    it costs ~17 requests instead of hundreds. Each entry also carries a
    `<lastmod>` date, which the scraper records as `sitemap_lastmod` so a future
    run can re-fetch only what changed.

- **OpenExchangeRates**

    Provider of historical and up-to-date currency exchange rates. Used to convert price data to a single currency. They offer free API access limited to 1000 requests per month.

- **US Consumer Price Index** (`data/external/consumer_price_index.csv`)

    CPI for All Urban Consumers (CPI-U), US city average, all items, not
    seasonally adjusted. Used to express historical prices in constant dollars.
    Published by the [Bureau of Labor Statistics](https://www.bls.gov/cpi/data.htm).

- **Geocoding API** (planned, not yet used)

    A free geocoding API from [Map Maker](https://maps.co/), intended to resolve
    roaster and origin locations to coordinates for spatial analysis. No code in
    the pipeline calls it yet.

## Code Layout

Everything importable lives in the `coffee/` package; the pipeline steps are
installed as console commands that wrap it.

**`coffee/`**

- `sitemap.py` — discovers every review URL from the site's XML sitemaps, along
  with each one's `<lastmod>` date. Fails loudly (`SitemapError`) rather than
  returning a partial list.
- `fetch.py` — shared async HTTP GET with bounded concurrency and retry, used by
  both discovery and scraping.
- `parser.py` — turns one review's HTML into structured fields.
- `review.py` — fetches a single review page and parses it into a record.
- `clean.py` — the cleaned layer: types, price/currency/quantity parsing,
  origin and roaster locations, and the roaster crosswalk. Depends on the raw
  scrape and nothing else.
- `enrich.py` — the step after cleaning: USD conversion at the review month's
  rate and inflation adjustment. Separate because it needs reference data the
  reviews do not carry. Every step in both modules is a pure
  `DataFrame -> DataFrame` function; reference data is passed in, not read from
  disk.
- `pipeline.py` — the full run: discovers every review URL and fetches only
  those that are new or have changed since the last run.
- `storage.py` — where reviews live. `CsvReviewStore` keeps
  `data/raw/reviews.{csv,json}`; the pipeline talks to the protocol, so a
  database backend can replace it without touching the scrape.
- `config.py` — configuration, paths, and API keys (loaded from the environment
  / `.env`).
- `exchange_rates.py` — fetches historical rates for the scraped review dates.
- `roaster_resolution.py` — entity resolution for messy roaster names. Uses
  the roaster's name *and* location, and applies previously adjudicated pairs
  so manual effort accumulates rather than resetting. Outputs live in
  `data/roasters/` (see [Resolving roaster names](#resolving-roaster-names)
  for the workflow and [`docs/roaster-resolution.md`](docs/roaster-resolution.md)
  for why it is built this way).
- `cli.py` — argument parsing for the console commands below.

**`tests/`**

- `fixtures/html/` — ten saved review pages; `fixtures/parsed_reviews.json`
  pins their expected parse.
- `generate_golden.py` — regenerates that golden file after a deliberate
  parser change.

**`docs/`**

- [`roaster-resolution.md`](docs/roaster-resolution.md) — the design reasoning
  behind roaster entity resolution: why it optimises for precision, how the
  location signal is used, and how to tune the thresholds.

## Usage

Run from the repository root. `uv run` executes commands inside the project's
virtual environment without needing to activate it:

```bash
# Update data/raw/reviews.{csv,json}, fetching only what changed
uv run scrape-reviews

# Fetch historical exchange rates for any review months not already held
uv run fetch-exchange-rates

# Resolve roaster-name variants into a canonical crosswalk
uv run resolve-roasters data/raw/reviews.csv --outdir data/roasters

# Build the cleaned layer from the raw scrape
uv run clean-reviews

# Launch Jupyter for the analysis notebooks
uv run jupyter lab
```

`--help` on any of them lists the available options.

### Scraping is incremental

`data/raw/reviews.csv` is the single source of truth, updated in place. Git
holds the history, so the filename carries no date.

Discovery reads `sitemap_index.xml` (~17 requests) and returns every review URL
with its `<lastmod>`. A run fetches only the URLs that are new, that changed
since the last run, or whose freshness cannot be proven. On the current corpus
that is a handful of pages in about a second, against ~9,300 pages and half an
hour for a full pass.

Each fetched row records `scraped_at`, which gives the age of any given row.
Reviews that disappear from the sitemap are reported and **kept**: they cannot
be fetched again, so the held copy is the only one.

Re-fetch everything with:

```bash
uv run scrape-reviews --full
```

Use it after changing `coffee/parser.py`. An incremental run re-parses only the
pages it re-fetches, so without `--full` a parser fix reaches new rows only and
the corpus becomes a mixture of two parser versions. `scraped_at` is what makes
such a mixture visible.

### Exchange rates are incremental too

`fetch-exchange-rates` reads the review months from the cleaned layer, since
those are the months enrichment converts, and requests only the ones not
already present in `data/external/openex_exchange_rates.json`. Rates for a past
date do not change, so a date once held is never asked for again. Re-running a
current file costs zero requests, which matters against a free-tier limit of
1000 per month and a corpus spanning 323 distinct months.

The cleaned layer is built from the raw scrape alone, so this reads a file that
already exists rather than one it is needed to produce. The order is scrape,
clean, fetch rates, enrich. Rates can also be fetched before a cleaned layer
exists, by reading months from the raw scrape:

```bash
uv run fetch-exchange-rates -i data/raw/reviews.csv
```

Two properties protect the stored file, which is the only copy of that data:

- a failed request is never written, so a rate-limited run cannot replace
  populated dates with blanks
- results are checkpointed during the run, so an interrupted one keeps the
  requests it already spent

A date whose request failed is left unheld and retried next run. `--refetch`
re-requests everything, and exists only for repairing a corrupt file.

## Resolving roaster names

The same roaster is spelled many ways: `Onyx Coffee Lab` / `Onyx Coffee Lab
LLC` / `onyx coffee lab`. `resolve-roasters` groups the spellings and records
which pairs have already been judged, so the manual work shrinks with each run
rather than starting over.

### The three files

| file | edit it? | role |
|---|---|---|
| `roaster_decisions.csv` | **yes** | Source of truth: pairs that have been adjudicated. The only file here that cannot be regenerated. Commit it. |
| `roaster_review_queue.csv` | **yes** — the `verdict` column only | Pairs the tool could not decide. Regenerated every run. |
| `roaster_crosswalk.csv` | **no** | The output: `raw_name → canonical_name`. Regenerated every run; hand edits are overwritten. Commit it so downstream joins are reproducible. |

### The loop

**1. Resolve.**

```bash
uv run resolve-roasters data/raw/reviews.csv --outdir data/roasters
```

It prints four lines. Read them in this order:

```
1687 distinct spellings -> 1498 roasters (189 merged)     did it do anything?
no decisions yet (…/roaster_decisions.csv not found)       are your past calls applied?
17 pairs queued for review -> …/roaster_review_queue.csv   how much is left to judge?
12 rows in chain-risk clusters  <-- INSPECT THESE          did clustering misbehave?
```

**2. Judge the queue.**

Open `data/roasters/roaster_review_queue.csv` and answer in the `verdict`
column. That column is the only thing you change.

`merge` / `y` / `yes` / `m` / `same` / `1` all mean **same company**;
`split` / `n` / `no` / `s` / `different` / `0` all mean **different**. Anything
else is reported as an error rather than skipped, so a typo cannot silently
discard a row.

| name_a | name_b | score | location_evidence | verdict |
|---|---|---|---|---|
| Boyd Coffee | Boyds Coffee | 88.9 | `same` | `merge` |
| Fellow Coffee | Mellow Coffee | 83.3 | `neutral` | `split` |

`location_evidence` is the shortcut:

- **`same`** — one address. Usually the same company, but not always: `Wei Chuan
  Foods` and `Tehmag Foods` share a city and are unrelated. Read the names.
- **`neutral`** — same region, different city. Usually different companies.
- **`unknown`** — no location on one side. Judge on the names alone.

Blank rows are fine; they simply come back next time.

**Record answers before re-running.** Step 1 regenerates the queue, so a run
without `--accept-reviewed` would overwrite it. The command refuses to do that
while unrecorded verdicts are present and names the flag to use; filling in the
queue should always be followed by step 3.

**3. Record the verdicts and re-resolve.**

```bash
uv run resolve-roasters data/raw/reviews.csv --outdir data/roasters --accept-reviewed --decided-by "$USER"
```

This folds the answers into `roaster_decisions.csv`, then re-resolves with them
applied. The queue returns holding only the rows left blank.

**4. Commit all three files.** `roaster_decisions.csv` matters most, being the
only one that cannot be rebuilt.

### On the next scrape

Run step 1 against the new file. The queue contains only pairs not yet judged;
everything already decided stays decided. A pair that has been answered
reappearing indicates `roaster_decisions.csv` is missing from `--outdir`.

### Occasional extras

Inspect the chain-risk clusters, which single-linkage could only have assembled
transitively and are therefore the likeliest false merges:

```bash
uv run python -c "import pandas as pd; c=pd.read_csv('data/roasters/roaster_crosswalk.csv'); print(c[c.chain_risk][['raw_name','canonical_name','min_internal_score']].to_string(index=False))"
```

Surface merges the name score alone misses: pairs below the normal floor that
share an address, which is how `Starbucks` ~ `Starbucks Reserve Roastery` (score
72) is found. Such pairs are queued for judgement and never merged. The queue
roughly doubles, from 17 to 30 on the current data:

```bash
uv run resolve-roasters data/raw/reviews.csv --outdir data/roasters --location-review 70
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
collaboration. The run reports this:

```
!! 1 cluster(s) VIOLATE a split decision -- these names were kept together
   despite your verdict:
    RND  |  RND & Red Rooster Coffee Roaster  |  Red Rooster Coffee Roaster
```

The fix is to record a split against the bridging name as well: here, `RND`
against `RND & Red Rooster Coffee Roaster`. Rows in an affected cluster are also
marked `violates_decision` in the crosswalk.

### Two rules

1. **Never hand-edit `roaster_crosswalk.csv`.** It is regenerated on every run.
   To change a grouping, change the decision that produced it.
2. **To reverse a call, edit `roaster_decisions.csv` directly.**
   `--accept-reviewed` will not overwrite an existing verdict, so a change of
   mind shows up as a visible diff rather than happening silently.

### How it decides, briefly

Two signals. **Name**: normalize (accents, punctuation, legal suffixes, word
order), then exact-key collision, then fuzzy score. **Location**: populated on
nearly every review and close to independent of spelling, so it settles most of
what the name alone cannot; it resolved 41 of 50 queued pairs on the current
corpus.

The two directions are not symmetric:

- a **region conflict vetoes** a merge (`Heart Coffee Roasters` in Portland vs
  `Heat Coffee` in Taipei score 88.9 on name alone)
- a **matching location only surfaces** a pair for review and never merges it,
  because the score does not separate the correct cases from the incorrect:
  `Great Value (Walmart)`/`Great Value (Wal-Mart)` scores 82.1 and is right,
  `Tehmag Foods`/`Wei Chuan Foods` scores 82.9 and is wrong.

Both follow from the asymmetry of the errors: a false merge is silent and
affects every downstream average, while a false split is visible as soon as a
roaster appears twice in a table. See
[`docs/roaster-resolution.md`](docs/roaster-resolution.md) for the full
reasoning.

## The two layers

```
data/raw/reviews.csv     RAW      as scraped, never edited
data/clean/reviews.csv   CLEANED  typed, parsed, roasters resolved
```

The cleaned layer depends on the raw scrape **and nothing else**, so it can be
rebuilt on a fresh checkout with no external data. Putting prices in comparable
money needs historical exchange rates and CPI, and those are fetched using the
cleaned layer's own review months — so folding them into cleaning would have
made the cleaned layer unbuildable until the rates existed.

That conversion lives in `coffee/enrich.py` and runs after cleaning:

```python
from coffee.enrich import enrich_reviews, load_cpi, load_exchange_rates

priced = enrich_reviews(df, exchange_rates=..., cpi=...)
```

It adds `price_usd`, `price_usd_adj` and `price_usd_adj_per_lb`, leaving
`price_value`, `price_currency` and `quantity_in_lbs` as the cleaned layer
recorded them. There is no `enrich-reviews` command and no enriched layer on
disk yet; the notebooks apply it in memory.

**Field names are settled at the raw boundary, not later.** `coffee/parser.py`
normalises each scraped table label (`"Est. Price:"` → `est_price`) as it parses,
so the raw layer lands with the names the rest of the project uses. The scraped
label is presentation; the field name is schema. Cleaning therefore concerns
data only: `clean_reviews` begins by asserting the names are already correct
rather than fixing them, so a file predating this fails at the boundary with a
message naming the offending columns instead of failing inside a merge several
steps later.

`uv run clean-reviews` builds the second from the first. The transformation
lives in `coffee/clean.py` rather than in a notebook, so it is tested and runs
in CI.

What cleaning does:

- parses `est_price` into a value, an ISO 4217 currency and a quantity
- converts quantities to pounds, so prices are comparable per unit
- resolves origin and roaster locations to countries, and US states
- adds `roaster_canonical` from the roaster crosswalk, **keeping** the raw
  spelling beside it

What enrichment adds on top:

- `price_usd`, converted at the **review month's** exchange rate
- `price_usd_adj`, in a baseline month's dollars (default 2024-06), so a 1997
  price and a 2026 price can be compared
- `price_usd_adj_per_lb`, the comparable figure

Rows whose agtron reading exceeds 100 are dropped as site typos; the count is
reported on every run. Formats that are not whole-bean coffee (capsules, pods)
keep their review but get no quantity, since a price per pound would be
meaningless.

## Notebooks

Run them in order; each depends on the previous one's output.

| notebook | reads | writes |
|---|---|---|
| `01-data-cleaning` | `data/raw/reviews.csv` | `data/clean/reviews.csv` (same as `clean-reviews`) |
| `02-data-EDA` | `data/clean/reviews.csv`, enriched in memory | charts |
| `03-text-features` | `data/clean/reviews.csv` | wordclouds in `imgs/` |

`data/clean/reviews.csv` is gitignored; notebook 01 regenerates it, so run that
first on a fresh checkout. Committed data is limited to the scrape itself and to
outputs carrying human judgement, namely the roaster crosswalk and decisions.

Notebook 03 needs two data downloads that are not Python packages. It fetches
the NLTK corpora itself; the spaCy model is installed once:

```bash
uv run python -m spacy download en_core_web_sm
```

Notebook outputs are cleared before committing, since they reached 13MB of
embedded images against an already-large history. The figures worth keeping are
written to `imgs/`.

## Tests

```bash
uv run pytest
```

`tests/fixtures/html/` holds ten real review pages and
`tests/fixtures/parsed_reviews.json` pins their expected parse, so that a parser
regression fails a test rather than emptying a column unnoticed. After a
deliberate parser change, regenerate the golden file and read the diff:

```bash
uv run python tests/generate_golden.py
```

The project uses pre-commit for linting (ruff), formatting (ruff-format) and
type checking (mypy). Install the hooks once after cloning:

```bash
uv run pre-commit install
```

GitHub Actions runs the same checks on every pull request, plus the test suite
on Python 3.12 and 3.13. The tests make no network requests, so they are
reproducible offline.

## References

- [OpenExchangeRates API](https://docs.openexchangerates.org/reference/api-introduction)
- [BLS Consumer Price Index data](https://www.bls.gov/cpi/data.htm)
- [How to Use t-SNE Effectively](https://distill.pub/2016/misread-tsne/) — relevant to the embedding work in `03-text-features.ipynb`
- `notes/how_coffee_review_works.md` — how CoffeeReview scores coffees
