
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
- `pipeline.py` — the full run: discovers every review URL, scrapes each one,
  and writes a dated CSV + JSON to `data/raw/`.
- `config.py` — configuration, paths, and API keys (loaded from the environment
  / `.env`).
- `exchange_rates.py` — fetches historical rates for the scraped review dates.
- `roaster_resolution.py` — entity resolution for messy roaster names. Uses
  the roaster's name *and* location, and applies previously adjudicated pairs
  so manual effort accumulates rather than resetting. Outputs live in
  `data/processed/` (see [Resolving roaster names](#resolving-roaster-names)).
- `cli.py` — argument parsing for the console commands below.

**`tests/`**

- `fixtures/html/` — ten saved review pages; `fixtures/parsed_reviews.json`
  pins their expected parse.
- `generate_golden.py` — regenerates that golden file after a deliberate
  parser change.

## Usage

Run from the repository root. `uv run` executes commands inside the project's
virtual environment without needing to activate it:

```bash
# Scrape all reviews into data/raw/<YYYY-MM-DD>_reviews.{csv,json}
# Discovery reads sitemap_index.xml (~17 requests for the whole corpus).
uv run scrape-reviews

# Fetch historical exchange rates for the dates in a scraped file
uv run fetch-exchange-rates -i data/raw/<date>_reviews.csv

# Resolve roaster-name variants into a canonical crosswalk
uv run resolve-roasters data/raw/<date>_reviews.csv --outdir data/processed

# Launch Jupyter for the analysis notebooks
uv run jupyter lab
```

`--help` on any of them lists the available options.

## Resolving roaster names

The same roaster is spelled many ways — `Onyx Coffee Lab` / `Onyx Coffee Lab
LLC` / `onyx coffee lab`. `resolve-roasters` groups the spellings and records
which ones you have already judged, so the manual work shrinks each run instead of
starting over.

### The three files

Know which of these you edit and which you never touch:

| file | you edit it? | role |
|---|---|---|
| `roaster_decisions.csv` | **yes** | Source of truth: pairs that have been adjudicated. The only file here that cannot be regenerated. Commit it. |
| `roaster_review_queue.csv` | **yes** — the `verdict` column only | Pairs the tool could not decide. Regenerated every run. |
| `roaster_crosswalk.csv` | **no** | The output: `raw_name → canonical_name`. Regenerated every run; hand edits are overwritten. Commit it so downstream joins are reproducible. |

### The loop

**1. Resolve.**

```bash
uv run resolve-roasters data/raw/2026-09-19_reviews.csv --outdir data/processed
```

It prints four lines. Read them in this order:

```
1687 distinct spellings -> 1498 roasters (189 merged)     did it do anything?
no decisions yet (…/roaster_decisions.csv not found)       are your past calls applied?
17 pairs queued for review -> …/roaster_review_queue.csv   how much is left to judge?
12 rows in chain-risk clusters  <-- INSPECT THESE          did clustering misbehave?
```

**2. Judge the queue.**

Open `data/processed/roaster_review_queue.csv` and answer in the `verdict`
column. That column is the only thing you change.

`merge` / `y` / `yes` / `m` / `same` / `1` all mean **same company**;
`split` / `n` / `no` / `s` / `different` / `0` all mean **different**. Anything
else is reported as an error rather than skipped, so a typo cannot cost you a
session of answers.

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

**Save your answers before re-running.** Step 1 regenerates the queue, so a run
without `--accept-reviewed` would overwrite it. The tool refuses to do that
while unsaved verdicts are present and tells you which flag to use, but the
habit to build is: fill in the queue, then always go to step 3.

**3. Record the verdicts and re-resolve.**

```bash
uv run resolve-roasters data/raw/2026-09-19_reviews.csv --outdir data/processed --accept-reviewed --decided-by "$USER"
```

This folds your answers into `roaster_decisions.csv`, then re-resolves with them
applied. The queue comes back holding only what you left blank.

**4. Commit all three files**, `roaster_decisions.csv` above all — it is the only
one that cannot be rebuilt.

### On the next scrape

Run step 1 against the new file. The queue contains **only pairs you have never
judged**; everything already decided stays decided. If a pair you answered comes
back, something is wrong — check that `roaster_decisions.csv` is present in
`--outdir`.

### Occasional extras

Inspect the chain-risk clusters — the ones single-linkage could only have
assembled transitively, so the likeliest false merges:

```bash
uv run python -c "import pandas as pd; c=pd.read_csv('data/processed/roaster_crosswalk.csv'); print(c[c.chain_risk][['raw_name','canonical_name','min_internal_score']].to_string(index=False))"
```

Hunt for merges the name score alone misses — pairs below the normal floor that
share an address (this found `Starbucks` ~ `Starbucks Reserve Roastery`, which
scores 72). They are surfaced for judgement, never merged — expect the queue to
roughly double, 17 to 30 on the current data:

```bash
uv run resolve-roasters data/raw/2026-09-19_reviews.csv --outdir data/processed --location-review 70
```

### ### When a split doesn't stick

Occasionally you will split a pair and they stay together. That is not a bug in
your verdict — single-linkage can rejoin two names through a **third** name that
resembles both, most often a collaboration:

```
RND                               -> key 'rnd'
Red Rooster Coffee Roaster        -> key 'red rooster'
RND & Red Rooster Coffee Roaster  -> key 'red rnd rooster'   superset of both
```

Blocking the direct union doesn't help, because they rejoin through the collab.
The run says so explicitly:

```
!! 1 cluster(s) VIOLATE a split decision -- these names were kept together
   despite your verdict:
    RND  |  RND & Red Rooster Coffee Roaster  |  Red Rooster Coffee Roaster
```

The fix is to split against the **bridging** name too — here, `RND` vs
`RND & Red Rooster Coffee Roaster`. Rows in an affected cluster are also marked
`violates_decision` in the crosswalk.

### Two rules

1. **Never hand-edit `roaster_crosswalk.csv`.** It is regenerated on every run.
   To change a grouping, change the decision that produced it.
2. **To reverse a call, edit `roaster_decisions.csv` directly.**
   `--accept-reviewed` will not overwrite an existing verdict, so a change of
   mind shows up as a visible diff rather than happening silently.

### How it decides, briefly

Two signals. **Name**: normalize (accents, punctuation, legal suffixes, word
order), then exact-key collision, then fuzzy score. **Location**: populated on
nearly every review and almost independent of spelling, so it settles most of
what the name alone cannot — it resolved 41 of 50 queued pairs on the first real
run.

The two directions are deliberately not symmetric:

- a **region conflict vetoes** a merge (`Heart Coffee Roasters` in Portland vs
  `Heat Coffee` in Taipei score 88.9 on name alone)
- a **matching location only surfaces** a pair for review, never merges it,
  because the score cannot separate the good cases from the bad — `Great Value
  (Walmart)`/`Great Value (Wal-Mart)` scores 82.1 and is right, `Tehmag
  Foods`/`Wei Chuan Foods` scores 82.9 and is wrong.

This follows from the asymmetry of the errors: a false merge is silent and
corrupts every downstream average, while a false split is obvious the moment a
roaster appears twice in a table. See the module docstring in
`coffee/roaster_resolution.py` for the full reasoning.

## Tests

```bash
uv run pytest
```

`tests/fixtures/html/` holds ten real review pages and
`tests/fixtures/parsed_reviews.json` pins their expected parse, so that a
silent parser regression fails a test instead of quietly emptying a column —
see the module docstring in `tests/test_parser.py` for why that matters here.
After a deliberate parser change, regenerate the golden file and read the diff:

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
