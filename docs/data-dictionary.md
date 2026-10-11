# Data dictionary

[← README](../README.md) · [All documentation](README.md)

Every file the pipeline reads or writes, and every field in it.
[`data-flow.md`](data-flow.md) shows which command writes each file and which
reads it.

Completeness figures ("Filled") are the share of rows with a value, measured in
October 2026. They change as reviews are added.

## Files

| File | One row per | Key | Committed | Written by |
|---|---|---|---|---|
| [`data/downloaded/pages/*.html.gz`](#datadownloaded) | review page | file name | no | `scrape-reviews` |
| [`data/downloaded/manifest.jsonl`](#datadownloaded) | page download | `url` (last line wins) | no | `scrape-reviews` |
| [`data/parsed/reviews.csv`](#dataparsedreviewscsv) | review | `url` | yes | `parse-reviews` |
| [`data/reference/exchange_rates.json`](#datareferenceexchange_ratesjson) | month | `YYYY-MM-01` | yes | `fetch-exchange-rates` |
| [`data/reference/cpi.csv`](#datareferencecpicsv) | year | `Year` | yes | `fetch-cpi` |
| [`data/roasters/crosswalk.csv`](#dataroasterscrosswalkcsv) | roaster spelling | `raw_name` | yes | `resolve-roasters` |
| [`data/roasters/review_queue.csv`](#dataroastersreview_queuecsv) | pair of spellings to judge | `name_a`, `name_b` | yes | `resolve-roasters` |
| [`curated/roaster_decisions.csv`](#curatedroaster_decisionscsv) | judged pair | `name_a`, `name_b` | yes | by hand, and `resolve-roasters --accept-reviewed` |
| [`data/cleaned/reviews.parquet`](#datacleanedreviewsparquet) | review | `url` | no | `clean-reviews` |

## Conventions

- **Missing values.** In CSV files a missing value is an empty cell. In the
  cleaned Parquet file it is null, except in the location columns noted below,
  which use an empty string.
- **Text as printed.** The parsed reviews keep every value as the site printed
  it, as text. Cleaning converts types; the parsed file does not.
- **Columns that vary by vintage.** The fields on a review page have changed
  over the years, so many columns are empty for older or newer reviews. A field
  added in 2022 is empty for every review before it.
- **Dates.** Review months are the first day of the month. Timestamps are
  ISO 8601 in UTC.

---

## `data/downloaded/`

The review pages exactly as the site served them, gzipped, one file per review
in `pages/`. Each file is named after the review's URL slug:
`https://www.coffeereview.com/review/some-coffee/` is saved as
`some-coffee.html.gz`.

`manifest.jsonl` records each download as one JSON object per line. It is
append-only, so a page downloaded twice has two lines, and the last one is
current.

| Field | Type | Meaning |
|---|---|---|
| `url` | text | The review's URL. |
| `file` | text | The saved file's name in `pages/`. |
| `sitemap_lastmod` | date | When the site last changed the review, from the sitemap's `<lastmod>`. `null` if the sitemap gave none. |
| `fetched_at` | timestamp | When the page was downloaded. |

---

## `data/parsed/reviews.csv`

One row per review, parsed from its saved page. Every value is text as the site
printed it. Rows are added or updated, never removed, so a review deleted from
the site keeps its row.

### Identity and provenance

| Field | Filled | Meaning |
|---|---|---|
| `url` | 100% | The review's URL. Unique. |
| `sitemap_lastmod` | 100% | When the site last changed the review (`YYYY-MM-DD`). |
| `scraped_at` | 100% | When the saved page this row was parsed from was downloaded. |
| `review_date` | 100% | The month the review was published, as printed: `January 2026`. |

### The coffee and the roaster

| Field | Filled | Meaning |
|---|---|---|
| `title` | 100% | The coffee's name as reviewed: `Ethiopia Guji Natural`. |
| `roaster` | 100% | The roaster's name as printed on this review. The same roaster can be spelled several ways; see the crosswalk. |
| `roaster_location` | 99% | Where the roaster is, most specific part first: `Portland, Oregon`, `Taipei, Taiwan`. |
| `coffee_origin` | 94% | Where the coffee was grown, in free text: `Huehuetenango growing region, Guatemala`. Blends name several origins. |
| `roast_level` | 94% | One of `Light`, `Medium-Light`, `Medium`, `Medium-Dark`, `Dark`, `Very Dark`. |
| `agtron` | 99% | Agtron roast-color readings as `external/ground`, such as `59/75`: the whole beans, then the ground coffee. Higher is lighter. The site prints `0` for a reading it did not take: `0/44`, `0/0`. |
| `est_price` | 77% | The estimated retail price as printed: `$19.95/12 ounces`, `NT $500/227 grams`. Reviews before 2010 have none. |
| `estimated_price` | <1% | The same field under the label "Estimated Price", used on some pages from 2022 to 2025. |

### Scores

The overall `rating` is on a 100-point scale and the sub-scores on a 1–10
scale. The rating is 50 plus the sum of the five sub-scores (aroma, acidity,
body, flavor, aftertaste), to within a point. A score the site did not give is
empty.

| Field | Filled | Meaning |
|---|---|---|
| `rating` | 100% | The overall score, out of 100. |
| `aroma` | 98% | Aroma, 1–10. |
| `acidity` | 40% | Acidity, 1–10. The site renamed this field `acidity/structure` in 2017–18; each review has one or the other. |
| `acidity/structure` | 44% | The same score under its newer name. |
| `body` | 99% | Body (mouthfeel), 1–10. |
| `flavor` | 99% | Flavor, 1–10. |
| `aftertaste` | 90% | Aftertaste (finish), 1–10. |
| `with_milk` | 13% | For espressos, how the coffee scores with milk, 1–10. |

### Review text

| Field | Filled | Meaning |
|---|---|---|
| `blind_assessment` | 100% | The tasting note, written while the coffee was tasted blind. |
| `notes` | 100% | Background on the coffee: the farm, variety, processing and roaster. |
| `bottom_line` | 55% | A one- or two-sentence summary. Rare before mid-2016. |

### Fields on recent pages only

Each of these is filled on the same 33 reviews, from 2022 to 2025.

| Field | Meaning |
|---|---|
| `processing_method` | How the fruit was removed and the beans dried: `Natural`, `Washed`. |
| `variety` | The coffee tree variety: `SL34`, `Gesha`. |
| `harvested` | When the coffee was harvested: `November 2022 to March 2023`. |
| `lot_size` | The size of the lot, often `Not disclosed`. |
| `lot_#_or_id` | The lot's identifier. |
| `seller` | Who sells the green coffee. |

---

## `data/reference/exchange_rates.json`

Historical exchange rates from OpenExchangeRates, one entry per review month
from January 1999, the start of the service's history.

```json
{
  "2024-07-01": {"EUR": 0.9234, "TWD": 32.51, "USD": 1.0, "...": "..."},
  "...": {}
}
```

Each month maps currency codes (ISO 4217) to units per US dollar on the first of
that month. Cleaning converts a price as `price_value / rate`. An empty entry,
`{}`, is a month whose request failed; it is requested again on the next run.

---

## `data/reference/cpi.csv`

The Consumer Price Index for All Urban Consumers (CPI-U), US city average, all
items, not seasonally adjusted: Bureau of Labor Statistics series
`CUUR0000SA0`. The index is 100 for the 1982–84 average. The layout is the
BLS's own: one row per year from 1990.

| Field | Meaning |
|---|---|
| `Year` | The year. |
| `Jan` … `Dec` | The index for that month. Empty if the BLS has not published it, or never did, as for October 2025. |
| `HALF1`, `HALF2` | The averages for January–June and July–December. Kept as found; `fetch-cpi` does not update them. |

Values are stored exactly as the BLS printed them, such as `208.490`.

---

## `data/roasters/crosswalk.csv`

Maps every roaster spelling in the reviews to one canonical name. Regenerated
on every run of `resolve-roasters`; edit the decisions file instead.

| Field | Meaning |
|---|---|
| `raw_name` | A spelling of a roaster's name, as it appears in `roaster`. Unique. |
| `n_records` | How many reviews use this spelling. |
| `cluster_id` | Groups the spellings judged to be one roaster. Reassigned on every run, so not stable between runs. |
| `canonical_name` | The name used for the whole cluster: its most common spelling, with ties going to the longer one. |
| `cluster_size` | How many spellings are in the cluster. |
| `min_internal_score` | The lowest name-similarity score (0–100) between any two spellings in the cluster. 100 for a cluster of one. |
| `chain_risk` | True when `min_internal_score` is below the auto-merge threshold (92 by default): the spellings were joined through a chain of similar names, not all directly. The likeliest false merges. |
| `violates_decision` | True when the cluster holds two names recorded as different roasters. See "When a split does not hold" in the README. |

---

## `data/roasters/review_queue.csv`

Pairs of spellings whose names are similar enough to check but not to merge
automatically. Regenerated on every run of `resolve-roasters`; only `verdict`
is meant to be edited.

| Field | Meaning |
|---|---|
| `name_a`, `name_b` | The two spellings. |
| `core_a`, `core_b` | Each name reduced to the key that was compared: lowercase, with common words such as "coffee" and "roasters" removed. |
| `score` | Name similarity, 0–100. |
| `location_a`, `location_b` | Each name's locations from the reviews, separated by `; `. |
| `location_evidence` | `same` (a shared location), `neutral` (same region, different city) or `unknown` (a name has no location). Pairs whose locations conflict are never queued. |
| `verdict` | Empty until judged. `merge` for the same roaster, `split` for different ones; the synonyms are listed in the README. |

---

## `curated/roaster_decisions.csv`

Every pair of spellings that has been judged. Kept by hand and committed; no
command can rebuild it.

| Field | Meaning |
|---|---|
| `name_a`, `name_b` | The two spellings. The order does not matter. |
| `verdict` | `merge` (same roaster) or `split` (different roasters). |
| `decided_by` | Who or what made the call, such as a name or a model run. |
| `decided_on` | When, as `YYYY-MM-DD`. |
| `note` | Optional reasoning. |

---

## `data/cleaned/reviews.parquet`

The table the notebooks read: one row per review, with types converted,
prices made comparable and roaster names resolved. It carries every column of
the parsed reviews except `agtron` and `acidity/structure`, which it replaces,
and adds the columns below. Reviews with an Agtron reading above 100 are
dropped as typos.

### Changed from the parsed reviews

| Field | Type | Meaning |
|---|---|---|
| `review_date` | datetime | The month the review was published, as its first day. |
| `sitemap_lastmod` | datetime | When the site last changed the review, as a date. |
| `scraped_at` | datetime (UTC) | When the saved page the row came from was downloaded. |
| `rating` | float | The overall score, out of 100. Ranges from 52 to 98, with half of all reviews from 90 to 94. Scores have risen over time: the median was 81 in the late 1990s and is 94 now. |
| `aroma`, `body`, `flavor`, `aftertaste` | float | Sub-scores, 1–10. Some early reviews used a different scale and hold fractional values such as 4.3. |
| `acidity` | float | Acidity, 1–10, taken from `acidity` or, when that is empty, `acidity/structure`. |
| `coffee_origin` | text | As parsed, lowercased. |
| `with_milk` | text | Unchanged from the parsed reviews. |

Values such as `NR` or `N/A` in a score become null.

### Roast

| Field | Type | Meaning |
|---|---|---|
| `agtron_external` | float | Agtron reading of the whole beans. Higher is lighter. |
| `agtron_ground` | float | Agtron reading of the ground coffee. |

An Agtron reading of 0 means the site printed no reading (about 380 reviews for
`agtron_external`, 130 for `agtron_ground`). Cleaning keeps it as 0, so treat 0
as missing.
| `is_espresso` | bool | True when the title mentions espresso or the review has a `with_milk` score. |

### Origin and roaster

| Field | Type | Meaning |
|---|---|---|
| `origin_country` | text | The countries named in `coffee_origin`, lowercase and separated by `;`: `colombia;ethiopia`. When no country matches, the `coffee_origin` text itself, so unresolved origins stay visible. Empty when there is no origin. |
| `roaster_country` | text | The roaster's country, lowercase: `united states`, `taiwan`. When the location matches no country or US state, the last part of it as written, such as `not disclosed`. Empty when there is no location. |
| `roaster_us_state` | text | For US roasters, the state, lowercase. Empty otherwise. |
| `roaster_canonical` | text | The roaster's canonical name from the crosswalk. The spelling itself when the crosswalk does not list it. `roaster` keeps the original. |

### Price as printed

| Field | Type | Meaning |
|---|---|---|
| `price_text` | text | The price exactly as printed, from `est_price` or, failing that, `estimated_price`. |
| `price_field` | text | Which of those two fields `price_text` came from. |
| `price_value` | float | The amount, in `price_currency`. |
| `price_currency` | text | The currency as an ISO 4217 code: `USD`, `TWD`, `CAD`. |
| `price_format` | text | What the price buys; see the table below. |
| `quantity_value` | float | The quantity, in `quantity_unit`. Only for `weight` prices. |
| `quantity_unit` | text | `ounces`, `grams`, `pounds` or `kilograms`. Only for `weight` prices. |
| `quantity_in_lbs` | float | The quantity in pounds. Only for `weight` prices. |

| `price_format` | Meaning |
|---|---|
| `weight` | Coffee sold by weight: `12 ounces`, `250 grams`. The only format with a price per pound. |
| `single_serve` | K-Cups, capsules, pods, brew bags. |
| `ready_to_drink` | Cans, bottles, cartons, concentrate. |
| `instant` | Instant coffee, sticks, tubes, sachets, packets. |
| `other` | Something else, such as a boxed set. |
| `unspecified` | A price with no quantity. |

Prices start in 2010; no earlier review has one. A few are far above the rest:
the median `weight` price is about $31 a pound in January 2026 dollars, and the
highest is over $10,000, for competition lots sold in small amounts.

### Comparable prices

These columns are filled only when cleaning has exchange rates and CPI data.
They are in each review month's own dollars; see
[Baseline dollars](#baseline-dollars) for adjusting them for inflation.

| Field | Type | Meaning |
|---|---|---|
| `price_usd` | float | The price in US dollars, at the rate for the review month. |
| `price_usd_per_lb` | float | `price_usd` per pound. Only for `weight` prices. |
| `cpi` | float | The CPI-U for the review month. Empty when the BLS has published none. |
| `price_status` | text | Whether the price can be adjusted for inflation, and if not, why; see below. Set on every row. |

| `price_status` | Meaning |
|---|---|
| `comparable` | Has `price_usd` and `cpi`, so it can be put in any baseline month's dollars. |
| `no_cpi` | Converted to dollars, but the BLS has published no CPI for the review month. |
| `no_exchange_rate` | No exchange rate for the currency in the review month. |
| `not_converted` | Cleaned without exchange rates and CPI. |
| `no_price` | The review names no price. |

### Baseline dollars

The cleaned dataset carries no baseline month. `to_baseline_dollars` in
`src/coffee/prices.py` adds these columns for a chosen month, which must be the
first day of a month the CPI table covers. The README's
[Comparable prices](../README.md#comparable-prices) section shows how to call it.

| Field | Type | Meaning |
|---|---|---|
| `price_usd_adj` | float | `price_usd × CPI(baseline) ÷ cpi`: the price in baseline dollars. Empty where `cpi` or `price_usd` is. |
| `price_usd_adj_per_lb` | float | `price_usd_per_lb`, scaled the same way. Only for `weight` prices. |
| `price_baseline_date` | datetime | The baseline month. Set only on rows that were adjusted. |
