
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

The same roaster is spelled many ways (`Onyx Coffee Lab` / `Onyx Coffee Lab LLC`
/ `onyx coffee lab`). `resolve-roasters` groups the spellings, using two
signals: the name, and the **roaster location** — which is populated on
essentially every review and is nearly orthogonal to spelling. A region
conflict vetoes a merge outright (`Heart Coffee Roasters` in Portland vs `Heat
Coffee` in Taipei score 88.9 on name alone); a matching location only *surfaces*
a pair for review, never merges it.

Three files, and the distinction between them matters:

| file | role |
|---|---|
| `roaster_decisions.csv` | **Source of truth.** Pairs you (or an LLM) adjudicated, with who decided and when. Hand-edited, committed. The only one that can't be regenerated. |
| `roaster_crosswalk.csv` | **Derived.** `raw_name → canonical_name`. Regenerated every run — never hand-edit it. |
| `roaster_review_queue.csv` | **Derived.** Only pairs with no decision yet, with a blank `verdict` column and each side's location. |

The loop, which is what makes manual effort shrink instead of resetting:

```bash
# 1. resolve; anything it can't decide lands in the review queue
uv run resolve-roasters data/raw/<date>_reviews.csv --outdir data/processed

# 2. open roaster_review_queue.csv and put `merge` or `split` in `verdict`

# 3. fold those answers into the decisions file and re-resolve
uv run resolve-roasters data/raw/<date>_reviews.csv --outdir data/processed \
    --accept-reviewed --decided-by "$USER"
```

Re-running re-derives the clusters but never re-asks an answered question, so
the queue trends toward zero. To hunt for matches the name score alone misses,
add `--location-review 70`: pairs scoring below the normal floor but sharing an
address get surfaced (never merged).

Two numbers to read after each run: **chain-risk rows** (clusters that could
only have been assembled transitively — inspect these first) and the **queue
size**.

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
