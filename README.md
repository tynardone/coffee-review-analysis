# Coffee Review Scraper and Analysis

![A cup of black coffee on a saucer beside a wooden scoop of roasted beans](imgs/cup-beans.jpeg)

[![Python](https://img.shields.io/badge/python-3.12%20%7C%203.13-blue.svg)](https://www.python.org/downloads/)
[![Code style: Ruff](https://img.shields.io/badge/code%20style-Ruff-000000.svg)](https://github.com/astral-sh/ruff)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A dataset and analysis of the roughly 9,300 blind-tasting reviews that
[CoffeeReview.com](https://www.coffeereview.com/) has published since 1997.

The project scrapes every review, cleans the results into a single table, and
analyzes how price and quality have changed over time. Prices are quoted in
about twenty currencies since 2010, so comparing them requires historical
exchange rates and inflation data. Roaster names are spelled inconsistently, so
comparing roasters requires entity resolution.

- [Installation](#installation)
- [Usage](#usage)
- [Data sources](#data-sources)
- [Data files](#data-files)
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

Settings come from three places, each overriding the one before:

1. `config/settings.toml`, committed: the defaults for the log level, scrape
   concurrency, the inflation baseline month and the roaster-matching
   thresholds, with a comment on each.
2. `.env` at the project root, not committed: your secrets and local overrides.
3. Environment variables, which win over both.

Command-line flags override all three for a single run.

The only secret is an [OpenExchangeRates](https://openexchangerates.org/signup/free)
app ID, used to fetch exchange rates. The free tier allows 1,000 requests a
month. Copy the template and fill it in:

```bash
cp .env.example .env
```

Every variable is named `COFFEE_` plus the setting's name, with a double
underscore for a value inside a TOML table: `COFFEE_OPENEXCHANGERATES_API_ID`,
`COFFEE_DATA_DIR` (default `data/`), `COFFEE_CURATED_DIR` (default `curated/`),
`COFFEE_SCRAPE__CONCURRENCY`. Settings are checked when a command starts, so a
bad value stops it immediately with a message naming the setting.

Settings are checked strictly:

- A name that matches no setting is an error. A typo such as
  `COFFEE_SCRAPE__CONCURENCY`, or a TOML table written `[roaster]`, stops the
  command. Lines in `.env`
  without the `COFFEE_` prefix are left alone, since they may belong to
  another tool.
- A relative `COFFEE_DATA_DIR` or `COFFEE_CURATED_DIR` is relative to the
  project root, not to the folder a command is run from, and `~` is expanded.
- The baseline month is given as its first day, such as `2026-01-01`, since
  CPI is monthly.

The settings file and the data live in the repository, so the package must be
installed from a checkout in editable mode, which `uv sync` does. Installed
any other way, it stops with an error.

Values that are part of the code's logic rather than a choice, such as site
URLs, timeouts and parsing rules, are constants next to the code that uses
them, not settings.

## Usage

Run commands from the repository root. `uv run` executes them in the project's
environment without activating it.

To bring the data up to date and open the notebooks:

```bash
uv run refresh-data
uv run jupyter lab
```

`refresh-data` runs six steps in dependency order, or five with `--skip-scrape`,
and reports on each. Every step is also a command in its own right:

| Step | Command | Writes |
|---|---|---|
| 1 | `uv run scrape-reviews` | `data/downloaded/` (saved HTML pages) |
| 2 | `uv run parse-reviews` | `data/parsed/reviews.csv` |
| 3 | `uv run resolve-roasters data/parsed/reviews.csv` | `data/roasters/crosswalk.csv` |
| 4 | `uv run fetch-exchange-rates` | `data/reference/exchange_rates.json` |
| 5 | `uv run fetch-cpi` | `data/reference/cpi.csv` |
| 6 | `uv run clean-reviews` | `data/cleaned/reviews.parquet` |

Each command documents its options under `--help`. The most useful options on
`refresh-data` are:

- `--full` re-downloads and re-parses every review, not only those that changed.
  Exchange rates and CPI stay incremental.
- `--skip-scrape` makes no requests to the review site; it parses and rebuilds
  from the pages already saved.
- `--baseline-date` sets the month whose dollars adjusted prices are expressed in.

`refresh-data` stops at the first step that fails, and the steps after it do
not run. The usual causes:

- No OpenExchangeRates key (step 4). Add it to `.env`, or run the steps
  around it on their own: `clean-reviews` builds the cleaned dataset without
  exchange rates, leaving prices in their original currencies.
- Answers in the roaster review queue that are not recorded yet (step 3). Run
  `resolve-roasters` with `--accept-reviewed`, as in
  [Resolving roaster names](#resolving-roaster-names).
- The site's sitemap cannot be read (step 1), so the list of reviews would be
  incomplete. Run again later, or pass `--skip-scrape`.
- The BLS API is unreachable (step 5). Run again later; the CPI table keeps
  what it holds.

Smaller failures do not stop the run. A review page that fails to download or
parse, or a month whose exchange rate fails to arrive, is reported and tried
again next time.

### Command options

Every path defaults to its place in [Data files](#data-files), and the other
defaults come from the [settings](#configuration). A flag overrides them for a
single run. `DATE` is the first day of a month, such as `2026-01-01`.

| Command | Options |
|---|---|
| `refresh-data` | `--full`, `--skip-scrape`, `--baseline-date DATE` |
| `scrape-reviews` | `--full`, `--limit N`, `-c`/`--concurrency N` |
| `parse-reviews` | `--full`, `--limit N` |
| `resolve-roasters INFILE` | `--column` (default `roaster`), `--location-column` (default `roaster_location`; `''` turns locations off), `--outdir`, `--decisions`, `--auto N`, `--review N`, `--location-review N`, `--accept-reviewed`, `--decided-by NAME` (default `manual`) |
| `fetch-exchange-rates` | `-i`/`--input`, `-o`/`--output`, `--refetch` |
| `fetch-cpi` | `-o`/`--output`, `--start-year YEAR` with `--end-year YEAR` |
| `clean-reviews` | `-i`/`--input`, `-o`/`--output`, `--crosswalk`, `--rates`, `--cpi`, `--baseline-date DATE` |

`resolve-roasters` is the only command with a required argument: the reviews
file whose roaster names it groups, normally `data/parsed/reviews.csv`.
`refresh-data` passes it for you.

The pipeline ends at the cleaned dataset; charts and aggregates live in the
notebooks. [`docs/data-flow.md`](docs/data-flow.md) maps every file the pipeline
reads and writes.

## Data sources

### CoffeeReview.com

Review URLs come from the site's `sitemap_index.xml`, which lists every review
in about 17 requests.

Each sitemap entry carries a `<lastmod>` timestamp: when the site last changed
the review. A run fetches a review when it is new, when either time is unknown,
or when it was changed after the saved copy was downloaded. A 24-hour lookback
(`[scrape] lookback_hours`) also re-fetches, once, any page edited shortly
before it was downloaded, in case the site's cache served the old version. That
is usually a handful of pages and about a second of work; a full pass is some
9,300 pages and half an hour.

Scraping and parsing are separate steps. `scrape-reviews` saves each page's
HTML, gzipped, to `data/downloaded/pages/`, and records it in
`data/downloaded/manifest.jsonl` with its sitemap date and fetch time.
`parse-reviews` reads the saved pages and updates `data/parsed/reviews.csv`,
without touching the network. By default it parses only pages that have no row
yet or were fetched again since their row was written.

After changing the parser (`src/coffee/review_page.py`), re-parse everything
from the saved pages. This takes about a minute and makes no requests:

```bash
uv run parse-reviews --full
```

Both commands take `--limit N` for a quick trial: `parse-reviews --full
--limit 20` checks a parser change on 20 pages before running it on all of them.

`data/parsed/reviews.csv` is updated in place, and git keeps its history. Rows are
only ever added or updated, never removed. Each row records in `scraped_at` when
the page it came from was fetched. Reviews that drop out of the sitemap are
reported but kept: they can no longer be fetched, so the stored copy is the only
one left.

The saved pages take about 250 MB and are not committed; back them up with the
rest of the disk. The first scrape into an empty `data/downloaded/` downloads every
page once, which takes about half an hour.

### Exchange rates

Historical daily rates from OpenExchangeRates convert each price to US dollars
at the rate for the month the review was published. `fetch-exchange-rates` reads
the review months from the parsed reviews and requests only those missing from
`data/reference/exchange_rates.json`. It reads the parsed reviews rather than
the cleaned ones because cleaning depends on these rates.

A past month's rate never changes, so each month is fetched once. A full fetch
takes one request per review month since 1999, about a third of the free tier's
monthly allowance, and re-running against an up-to-date file costs nothing. The
file is protected in two ways:

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
`data/reference/cpi.csv`, keeping the BLS layout of one row per
year and one column per month.

The keyless API returns the last three years and allows 25 requests a day, which
covers routine updates. To rebuild an older range, pass `--start-year` and
`--end-year` together, up to ten years per request. Published figures do not change, so
months already in the table are never overwritten, and the three-year window
cannot shrink a table that goes back to 1990.

Two quirks of the series are handled explicitly:

- Period `M13` is the annual average, not a month, and is discarded.
- A month the BLS never published appears as `-` and is left blank. October 2025
  is one: the index was not produced during that year's lapse in federal
  appropriations. Prices from such a month are kept but not adjusted or
  estimated, and are marked `no_cpi` (see [Comparable prices](#comparable-prices)).

## Data files

The folders under `data/` are named for what has happened to the data, in
pipeline order:

```
data/
  downloaded/          review pages as the site served them    not committed
    pages/*.html.gz
    manifest.jsonl
  parsed/
    reviews.csv        one row per review                      committed
  reference/
    exchange_rates.json                                        committed
    cpi.csv                                                    committed
  roasters/
    crosswalk.csv      spelling -> canonical roaster           committed
    review_queue.csv   pairs for you to judge                  committed
  cleaned/
    reviews.parquet    typed, priced, roasters resolved        not committed
curated/
  roaster_decisions.csv  your judgments; nothing regenerates it  committed
```

`downloaded/` keeps the HTML so that parsing can be redone without downloading
anything. `parsed/reviews.csv` is committed and kept
as plain text that any tool can read, because a review removed from the site
survives only there. `cleaned/reviews.parquet` is rebuilt by a single command
and read only by code, so it is Parquet: about a third of the size, and it
preserves column types that CSV would lose.

Every path is defined in `Settings` in `src/coffee/settings.py`, relative to
`COFFEE_DATA_DIR` or `COFFEE_CURATED_DIR`.
[`docs/data-dictionary.md`](docs/data-dictionary.md) lists every field in every
file, with its meaning.

### Field names

The parser normalizes each label in a review's spec table as it reads the page
(`"Est. Price:"` becomes `est_price`), so the parsed reviews already use the
project's column names. Cleaning checks this on entry and stops with an error
naming any unnormalized columns, instead of failing several steps later.

### Cleaning

`uv run clean-reviews` builds the cleaned layer. The logic lives in
`src/coffee/clean.py`. Cleaning:

- reads every price field the site has used into one `price_text`, and parses
  it into an amount, an ISO 4217 currency, a quantity and a format
- converts quantities to pounds
- resolves origins and roaster locations to countries, and US roasters to states
- adds `roaster_canonical` from the roaster crosswalk, alongside the original
  spelling

Reviews with an Agtron reading above 100 are typos on the site. They are dropped,
and each run reports how many.

### Prices as the site wrote them

The site has labeled the price "Est. Price" and, on some pages from 2022 to
2025, "Estimated Price". The parse step keeps each under its own name
(`est_price`, `estimated_price`), and cleaning reads both into `price_text`,
recording which one in `price_field`. Any other column with "price" in its name
is read too, with a warning to add it to `PRICE_FIELDS` in `clean.py`.

`src/coffee/price_text.py` reads the roughly two hundred ways the price is
written: `$19.00/16 ounces`, `NT $500/227 grams`, `£6.50 for 250g`, `$47.30/kg`,
typos such as `8 ouces`, and asides such as `(sold as a set of 3/$101.75`. Every
price keeps its amount and currency, whatever it buys. `price_format` says what
that is:

| `price_format` | Examples | Price per pound? |
|---|---|---|
| `weight` | `12 ounces`, `250 grams`, `1 kg`, `150-gram tin` | Yes |
| `single_serve` | K-Cups, capsules, pods, brew bags | No |
| `ready_to_drink` | cans, bottles, cartons, concentrate | No |
| `instant` | instant, sticks, tubes, sachets, packets | No |
| `other` | a boxed set | No |
| `unspecified` | a price with no quantity, such as `$15.00` | No |

Only `weight` prices get `quantity_value`, `quantity_unit` and
`quantity_in_lbs`, since a price per pound means nothing for a capsule. Prices
started appearing on the site in 2010; no earlier review has one.

### Comparable prices

When exchange rates and CPI data are available, cleaning also applies
`src/coffee/prices.py`, which adds:

- `price_usd`: the price in US dollars at the review month's rate
- `price_usd_adj`: that price in a baseline month's dollars, so a 2010 price can
  be compared with a 2026 one
- `price_usd_adj_per_lb`: the adjusted price per pound, for `weight` prices
- `price_baseline_date`: the baseline month
- `price_status`: why `price_usd_adj` is or is not filled

The original `price_value`, `price_currency` and `quantity_in_lbs` are kept.
Every price is converted and adjusted, whatever its format.

`price_status` is set on every review, with or without reference data, and
names the first step that failed:

| `price_status` | Meaning |
|---|---|
| `adjusted` | `price_usd_adj` holds the price in baseline dollars |
| `no_cpi` | converted to dollars, but the BLS has published no CPI for the review month |
| `no_exchange_rate` | no exchange rate for the currency in the review month |
| `not_converted` | cleaned without exchange rates and CPI |
| `no_price` | the page names no price |

The baseline is January 2026 by default. Change it for good with `baseline_date`
in `config/settings.toml`, or for one run with `--baseline-date`. It must be
the first day of a month the CPI table covers; any other date is refused. The baseline is
stored on every row because it is the unit of the adjusted price: the same coffee
costs $77.64 in January 2026 dollars and $61.58 in January 2020 dollars.

A month the CPI does not cover is never estimated. Its prices keep `price_usd`,
but `price_usd_adj`, `price_usd_adj_per_lb` and `price_baseline_date` stay
empty and `price_status` is `no_cpi`. That covers October 2025, which the BLS
never published, and the latest month or two, until the BLS releases them and
`fetch-cpi` picks them up.

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
| `roaster_decisions.csv` | `curated/` | Yes | Every pair that has been judged. The only file that cannot be regenerated. |
| `review_queue.csv` | `data/roasters/` | `verdict` column only | Pairs awaiting judgment. Regenerated on every run. |
| `crosswalk.csv` | `data/roasters/` | No | The output, mapping `raw_name` to `canonical_name`. Regenerated on every run. |

All three are committed. The crosswalk is committed so that joins against it are
reproducible.

### Workflow

**1. Resolve.**

```bash
uv run resolve-roasters data/parsed/reviews.csv
```

The summary answers four questions:

```
1688 distinct spellings -> 1497 roasters (191 merged)      did it do anything?
18 decisions applied from …/roaster_decisions.csv          are past calls applied?
0 pairs queued for review -> …/review_queue.csv    how much is left to judge?
11 rows in chain-risk clusters  <-- INSPECT THESE          did clustering misbehave?
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

## Notebooks

| Notebook | Reads | Produces |
|---|---|---|
| `01-data-cleaning` | the parsed reviews and reference data | `data/cleaned/reviews.parquet`, using the same code as `clean-reviews` |
| `02-data-EDA` | `data/cleaned/reviews.parquet` | exploratory charts |
| `03-text-features` | `data/cleaned/reviews.parquet` | word clouds in `imgs/` |

The cleaned dataset is not committed. On a fresh checkout, build it with
`uv run clean-reviews` or by running notebook 01 before opening the others.

Notebook 03 also needs NLTK corpora, which it downloads itself, and a spaCy
model, installed once:

```bash
uv run python -m spacy download en_core_web_sm
```

Notebook outputs are stripped on commit by the `nbstripout` pre-commit hook,
which keeps chart images out of git history.

Figures worth keeping go in `imgs/`. Git cannot store a new version of an image
as a small difference, so each changed PNG adds its full size to the history.
Commit a figure when you mean to update it. The word clouds use a fixed random
seed, so re-running notebook 03 on unchanged data rewrites them byte for byte
and git sees no change.

## Project layout

```
src/coffee/   the package
config/       settings.toml, the committed default settings
tests/        unit and integration tests, with saved review pages as fixtures
data/         pipeline inputs and outputs; committed except downloaded/ and cleaned/
curated/      files kept by hand that nothing can regenerate
docs/         data dictionary, data flow, roaster resolution design, roadmap
notebooks/    analysis
notes/        background reading: how CoffeeReview scores, clustering methods
```

The package lives under `src/`, so it can only be imported once installed. Tests,
notebooks and the console commands therefore all run the same code.

Each pipeline step has a module named after its command; the modules listed
under a step are the helpers it uses.

| Step | Module | Helpers |
|---|---|---|
| `scrape-reviews` | `scrape.py` | `sitemap.py`, `http_client.py`, `page_store.py` |
| `parse-reviews` | `parse.py` | `review_page.py`, `review_store.py` |
| `resolve-roasters` | `roasters/` | |
| `fetch-exchange-rates` | `exchange_rates.py` | `http_client.py` |
| `fetch-cpi` | `cpi.py` | `http_client.py` |
| `clean-reviews` | `clean.py` | `price_text.py`, `prices.py` |

- `scrape.py` discovers review URLs and downloads the pages that are new or
  changed. It never parses.
- `sitemap.py` finds every review URL with its `<lastmod>` time. It raises
  `SitemapError` instead of returning a partial list.
- `http_client.py` is the one place that talks HTTP, built on httpx: shared
  clients for the scrape (async) and the reference APIs (sync), one retry
  policy for transient errors, and a User-Agent naming the project.
- `page_store.py` saves and reads the downloaded pages and their manifest.
- `parse.py` turns saved pages into rows of `reviews.csv`. It never touches the
  network.
- `review_page.py` knows the layout of a review page and turns one into fields.
- `review_store.py` reads and writes `reviews.csv`. The parse step depends only
  on the `ReviewStore` interface, so a database could replace `CsvReviewStore`
  without changes to the parse.
- `clean.py` builds the cleaned layer.
- `price_text.py` reads one free-text price into an amount, a currency, a
  quantity and a format.
- `prices.py` handles currency conversion and inflation adjustment. It is
  separate because it is the only part of cleaning that needs outside data.
  Every step in both modules is a pure `DataFrame -> DataFrame` function that
  takes its reference data as an argument.
- `exchange_rates.py` and `cpi.py` fetch the reference data.
- `roasters/` resolves roaster names, one module per stage: `normalize`,
  `similarity`, `location`, `cluster`, `decisions` (the only one that touches
  disk) and `report`.
- `settings.py` defines `Settings`, the typed schema for every setting, and
  `get_settings()`, which loads and validates them once. Commands and notebooks
  call it and pass the values down; library code never reads the environment.
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
`tests/fixtures/parsed_reviews.json` records how each should parse, so a change
in parsing fails a test. After a deliberate
parser change, regenerate the file and review the diff:

```bash
uv run python -m tests.generate_golden
```

Ruff (linting and formatting), mypy and nbstripout run as pre-commit hooks.
Install them once after cloning:

```bash
uv run pre-commit install
```

Ruff and mypy run from the project's environment, at the versions locked in
`uv.lock`, so the hooks, CI and your editor agree. The package is checked with
`mypy --strict`; tests are type-checked without requiring annotations. To run
the checks by hand:

```bash
uv run ruff check .
uv run mypy
```

GitHub Actions runs the hooks, and the tests on Python 3.12 and 3.13, on every
pull request and every push to `main`.

## References

- [OpenExchangeRates API](https://docs.openexchangerates.org/reference/api-introduction)
- [BLS Consumer Price Index data](https://www.bls.gov/cpi/data.htm)
- [How to Use t-SNE Effectively](https://distill.pub/2016/misread-tsne/), background for the embedding work in `03-text-features.ipynb`
- [`notes/how_coffee_review_works.md`](notes/how_coffee_review_works.md): how CoffeeReview scores coffees
