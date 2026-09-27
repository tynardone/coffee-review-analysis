# Coffee Review Scraper and Analysis

[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-Ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A dataset and analysis of the roughly 9,300 blind-tasting reviews that
[CoffeeReview.com](https://www.coffeereview.com/) has published since 1997.

The project scrapes every review, cleans the results into a single table, and
analyzes how price and quality have changed over time. Two problems make this
more than a scrape. Prices are quoted in a dozen currencies across three
decades, so comparing them requires historical exchange rates and inflation
data. Roaster names are spelled inconsistently, so comparing roasters requires
entity resolution.

- [Installation](#installation)
- [Usage](#usage)
- [Data sources](#data-sources)
- [Data layers](#data-layers)
- [Resolving roaster names](#resolving-roaster-names)
- [Notebooks](#notebooks)
- [Project layout](#project-layout)
- [Development](#development)
- [References](#references)

## Installation

Requires Python 3.12 or 3.13 and [uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/tynardone/coffee-review-analysis.git
cd coffee-review-analysis
uv sync
```

`uv sync` creates a virtual environment in `.venv`, installs the locked
dependencies from `uv.lock`, and installs the `coffee` package in editable mode.
The `analysis` (notebooks) and `dev` (tooling) dependency groups are included by
default. For the pipeline alone, use `uv sync --no-default-groups`.

### Configuration

The only credential is an [OpenExchangeRates](https://openexchangerates.org/signup/free)
app ID, used to fetch exchange rates. The free tier allows 1,000 requests a
month. Copy the example file and fill it in:

```bash
cp .env.example .env
```

Variables already set in the environment take precedence over `.env`. Two
optional variables relocate the project's files: `COFFEE_DATA_DIR` (default
`data/`) and `COFFEE_SEEDS_DIR` (default `seeds/`).

## Usage

Run commands from the repository root. `uv run` executes them in the project's
environment without activating it.

To bring the data up to date and open the notebooks:

```bash
uv run refresh-data
uv run jupyter lab
```

`refresh-data` runs five steps in dependency order and reports on each. Every
step is also a command in its own right:

| Step | Command | Writes |
|---|---|---|
| 1 | `uv run scrape-reviews` | `data/raw/reviews.csv` |
| 2 | `uv run resolve-roasters data/raw/reviews.csv` | `data/roasters/roaster_crosswalk.csv` |
| 3 | `uv run fetch-exchange-rates` | `data/external/openex_exchange_rates.json` |
| 4 | `uv run fetch-cpi` | `data/external/consumer_price_index.csv` |
| 5 | `uv run clean-reviews` | `data/clean/reviews.parquet` |

Each command documents its options under `--help`. The most useful options on
`refresh-data` are:

- `--full` re-fetches and re-parses every review, not only those that changed.
- `--skip-scrape` rebuilds from the reviews already on disk without contacting
  the site.
- `--baseline-date` sets the month whose dollars adjusted prices are expressed in.

The pipeline ends at the cleaned dataset; charts and aggregates live in the
notebooks. [`docs/data-flow.md`](docs/data-flow.md) maps every file the pipeline
reads and writes.

## Data sources

### CoffeeReview.com

Review URLs come from the site's `sitemap_index.xml`, not its paginated
listings. The sitemap is more complete (9,333 review URLs against 9,054 from
pagination, with nothing found only in the listings) and takes about 17 requests
instead of several hundred.

Each sitemap entry carries a `<lastmod>` date, stored as `sitemap_lastmod`. A run
compares these dates with what is already held and fetches only reviews that are
new, have changed, or have no date to compare. That is usually a handful of
pages and about a second of work; a full pass is some 9,300 pages and half an
hour.

`data/raw/reviews.csv` is updated in place, and git keeps its history. Each row
records when it was fetched in `scraped_at`. Reviews that drop out of the sitemap
are reported but kept: they can no longer be fetched, so the stored copy is the
only one left.

After changing the parser (`src/coffee/parser.py`), run a full scrape:

```bash
uv run scrape-reviews --full
```

An incremental run re-parses only the pages it re-fetches, so without `--full` a
parser fix reaches new rows only and the corpus ends up mixing two parser
versions. `scraped_at` shows where such a mix exists.

### Exchange rates

Historical daily rates from OpenExchangeRates convert each price to US dollars
at the rate for the month the review was published. `fetch-exchange-rates` reads
the review months from the raw scrape and requests only those missing from
`data/external/openex_exchange_rates.json`. It reads the raw layer rather than
the cleaned one because cleaning depends on these rates.

A past month's rate never changes, so each month is fetched once. The corpus
spans 323 months, about a third of the free tier's monthly allowance, and
re-running against an up-to-date file costs nothing. The file is protected in
two ways:

- A failed request is never written, so a rate-limited run cannot blank out
  months already held. Failed months are retried on the next run.
- Progress is saved during the run, so an interrupted run keeps what it has
  already fetched.

`--refetch` requests every month again and is only needed to repair a corrupt
file.

### Consumer Price Index

Inflation adjustment uses CPI-U (US city average, all items, not seasonally
adjusted): series `CUUR0000SA0` from the Bureau of Labor Statistics public API,
which needs no key. `fetch-cpi` merges the series into
`data/external/consumer_price_index.csv`, keeping the BLS layout of one row per
year and one column per month.

The keyless API returns the last three years and allows 25 requests a day, which
covers routine updates. To rebuild an older range, pass `--start-year` and
`--end-year`, up to ten years per request. Published figures do not change, so
months already in the table are never overwritten, and the three-year window
cannot shrink a table that goes back to 1990.

Two quirks of the series are handled explicitly:

- Period `M13` is the annual average, not a month, and is discarded.
- A month the BLS never published appears as `-` and is left blank. October 2025
  is one: the index was not produced during that year's lapse in federal
  appropriations. Prices from such a month are left unadjusted, not dropped.

## Data layers

| Layer | File | Contents |
|---|---|---|
| Raw | `data/raw/reviews.csv` | Reviews as scraped. Never edited by hand. |
| Cleaned | `data/clean/reviews.parquet` | Typed and parsed, with roasters resolved and prices made comparable. |

The two formats are deliberate. The raw layer is irreplaceable, since a review
removed from the site cannot be scraped again, so it is kept as plain text that
any tool can read. The cleaned layer is rebuilt by a single command and read
only by code, so it is Parquet: about a third of the size, and it preserves
column types that CSV would lose.

### Field names

The parser normalizes each label in a review's spec table as it reads the page
(`"Est. Price:"` becomes `est_price`), so the raw layer already uses the
project's column names. Cleaning checks this on entry and stops with an error
naming any unnormalized columns, instead of failing several steps later.

### Cleaning

`uv run clean-reviews` builds the cleaned layer. The logic lives in
`src/coffee/clean.py`, not in a notebook, so it is covered by tests. Cleaning:

- parses `est_price` into an amount, an ISO 4217 currency and a quantity
- converts quantities to pounds
- resolves origins and roaster locations to countries, and US roasters to states
- adds `roaster_canonical` from the roaster crosswalk, alongside the original
  spelling

Reviews with an Agtron reading above 100 are typos on the site. They are dropped,
and each run reports how many. Products that are not whole-bean coffee sold by
weight, such as capsules and pods, keep their reviews but get no quantity, since
a price per pound would be meaningless.

### Comparable prices

When exchange rates and CPI data are available, cleaning also applies
`src/coffee/prices.py`, which adds:

- `price_usd`: the price in US dollars at the review month's rate
- `price_usd_adj`: that price in a baseline month's dollars, so a 1997 price can
  be compared with a 2026 one
- `price_usd_adj_per_lb`: the adjusted price per pound
- `price_baseline_date`: the baseline month

The original `price_value`, `price_currency` and `quantity_in_lbs` are kept.

The baseline defaults to January 2026 and is set with `--baseline-date`. It must
be a month the CPI table covers; any other month is refused. The baseline is
stored on every row because it is the unit of the adjusted price: the same coffee
costs $77.64 in January 2026 dollars and $61.58 in January 2020 dollars. Rows
from a month the CPI does not cover keep their unadjusted USD price and have no
baseline, which makes them easy to find.

Both reference files are optional. Without them, `clean-reviews` still builds
the cleaned layer, leaves prices in their original currencies, and prints a
warning. This lets it run on a fresh checkout.

## Resolving roaster names

The same roaster appears under many spellings: `Onyx Coffee Lab`,
`Onyx Coffee Lab LLC`, `onyx coffee lab`. `resolve-roasters` groups these into
one canonical name per roaster. Strong name matches are merged automatically
unless the locations conflict, and the uncertain middle goes to a queue for a
person to judge. Every judgment is recorded, so the manual work shrinks from
one run to the next. [`docs/roaster-resolution.md`](docs/roaster-resolution.md)
explains the design.

### Files

| File | Location | Edit? | Purpose |
|---|---|---|---|
| `roaster_decisions.csv` | `seeds/` | Yes | Every pair that has been judged. The only file that cannot be regenerated. |
| `roaster_review_queue.csv` | `data/roasters/` | `verdict` column only | Pairs awaiting judgment. Regenerated on every run. |
| `roaster_crosswalk.csv` | `data/roasters/` | No | The output, mapping `raw_name` to `canonical_name`. Regenerated on every run. |

All three are committed. The crosswalk is committed so that joins against it are
reproducible.

### Workflow

**1. Resolve.**

```bash
uv run resolve-roasters data/raw/reviews.csv
```

The summary answers four questions:

```
1687 distinct spellings -> 1496 roasters (191 merged)      did it do anything?
18 decisions applied from …/roaster_decisions.csv          are past calls applied?
0 pairs queued for review -> …/roaster_review_queue.csv    how much is left to judge?
11 rows in chain-risk clusters  <-- INSPECT THESE          did clustering misbehave?
```

**2. Judge the queue.** Open `data/roasters/roaster_review_queue.csv` and fill in
the `verdict` column. Leave every other column alone.

- Same company: `merge`, `yes`, `y`, `m`, `same` or `1`
- Different companies: `split`, `no`, `n`, `s`, `different` or `0`

Rows left blank come back on the next run. Any other value is reported as an
error, so a typo cannot quietly lose an answer.

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
uv run resolve-roasters data/raw/reviews.csv --accept-reviewed --decided-by "$USER"
```

This adds your answers to `seeds/roaster_decisions.csv` and resolves again with
them applied. The new queue holds only the rows you left blank.

Always follow step 2 with this step. A plain run regenerates the queue, which
would discard answers not yet recorded, so the command refuses to run while the
queue holds any and tells you to add `--accept-reviewed`.

**4. Commit the three files.**

After the next scrape, start again at step 1. Pairs already decided stay
decided, so the queue holds only new questions. If an answered pair reappears,
check that `seeds/roaster_decisions.csv` exists and that `--decisions` is not
pointing elsewhere.

### Checking the results

Chain-risk clusters were joined only through a chain of similar names, which
makes them the likeliest false merges. To list them:

```bash
uv run python -c "import pandas as pd; c=pd.read_csv('data/roasters/roaster_crosswalk.csv'); print(c[c.chain_risk][['raw_name','canonical_name','min_internal_score']].to_string(index=False))"
```

To find matches the name score misses, also queue lower-scoring pairs that share
an address. This is how `Starbucks` and `Starbucks Reserve Roastery` (score 72)
come to light. These pairs are only queued, never merged automatically. On the
current data this adds 13 pairs:

```bash
uv run resolve-roasters data/raw/reviews.csv --location-review 70
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

1. Never edit `roaster_crosswalk.csv` by hand; it is overwritten on every run. To
   change a grouping, change the decision behind it.
2. To reverse a decision, edit `seeds/roaster_decisions.csv` directly.
   `--accept-reviewed` never overwrites an existing verdict, so a change of mind
   always shows up as a diff.

## Notebooks

| Notebook | Reads | Produces |
|---|---|---|
| `01-data-cleaning` | the raw scrape and reference data | `data/clean/reviews.parquet`, using the same code as `clean-reviews` |
| `02-data-EDA` | `data/clean/reviews.parquet` | exploratory charts |
| `03-text-features` | `data/clean/reviews.parquet` | word clouds in `imgs/` |

The cleaned dataset is not committed. On a fresh checkout, build it with
`uv run clean-reviews` or by running notebook 01 before opening the others.

Notebook 03 also needs NLTK corpora, which it downloads itself, and a spaCy
model, installed once:

```bash
uv run python -m spacy download en_core_web_sm
```

Clear notebook outputs before committing; embedded images once pushed the
notebooks past 13 MB. Save figures worth keeping to `imgs/`.

## Project layout

```
src/coffee/   the package
tests/        unit and integration tests, with saved review pages as fixtures
data/         pipeline inputs and outputs; committed except data/clean/
seeds/        hand-kept reference data that nothing can regenerate
docs/         data flow and roaster resolution design
notebooks/    analysis
notes/        background on how CoffeeReview scores coffee
```

The package lives under `src/`, so it can only be imported once installed. Tests,
notebooks and the console commands therefore all run the same code.

- `sitemap.py` finds every review URL with its `<lastmod>` date. It raises
  `SitemapError` instead of returning a partial list.
- `fetch.py` is the shared asynchronous HTTP client, with bounded concurrency,
  retries on transient errors and common request headers.
- `parser.py` turns a review page into fields.
- `pipeline.py` runs a scrape: discovery, fetching what changed, and parsing.
- `storage.py` reads and writes the review store. The pipeline depends only on
  the `ReviewStore` interface, so a database could replace `CsvReviewStore`
  without changes to the scraper.
- `clean.py` builds the cleaned layer.
- `prices.py` handles currency conversion and inflation adjustment. It is
  separate because it is the only part of cleaning that needs outside data.
  Every step in both modules is a pure `DataFrame -> DataFrame` function that
  takes its reference data as an argument.
- `exchange_rates.py` and `cpi.py` fetch the reference data.
- `roasters/` resolves roaster names, one module per stage: `normalize`,
  `similarity`, `location`, `cluster`, `decisions` (the only one that touches
  disk) and `report`.
- `settings.py` defines `Settings`, which holds the data and seed directories.
  Each command or notebook builds one and passes paths down; library code never
  reads the environment directly.
- `cli.py` defines the console commands.

## Development

```bash
uv run pytest
```

Unit tests in `tests/unit/` mirror the package. Integration tests in
`tests/integration/` run the console commands against an empty temporary data
directory. The suite makes no network requests; a test that needs one must be
marked `network`, and CI skips those.

`tests/fixtures/html/` holds ten real review pages, and
`tests/fixtures/parsed_reviews.json` records how each should parse, so a parser
regression fails a test instead of quietly emptying a column. After a deliberate
parser change, regenerate the file and review the diff:

```bash
uv run python -m tests.generate_golden
```

Ruff (linting and formatting) and mypy run as pre-commit hooks. Install them
once after cloning:

```bash
uv run pre-commit install
```

GitHub Actions runs the hooks, and the tests on Python 3.12 and 3.13, on every
pull request and every push to `main`.

## References

- [OpenExchangeRates API](https://docs.openexchangerates.org/reference/api-introduction)
- [BLS Consumer Price Index data](https://www.bls.gov/cpi/data.htm)
- [How to Use t-SNE Effectively](https://distill.pub/2016/misread-tsne/), background for the embedding work in `03-text-features.ipynb`
- [`notes/how_coffee_review_works.md`](notes/how_coffee_review_works.md): how CoffeeReview scores coffees
