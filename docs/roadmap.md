# Roadmap

Where the project could go from here, in two halves. The data engineering half
turns the pipeline's output into a dataset ready for analysis. The data
analysis half uses that dataset: first a descriptive dashboard, then deeper
statistical, text and clustering work.

Nothing here is committed to. Each item carries one of three labels:

- **Next**: worth doing soon; later items depend on it.
- **Later**: valuable, once the items it depends on exist.
- **Maybe**: an idea worth keeping, with no plan to start.

The figures below were measured on the cleaned dataset in October 2026.

## Where the data stands

| | |
|---|---|
| Reviews | 9,326, from February 1997 to September 2026 |
| Score and review text | complete for every review |
| Sub-scores (aroma, body, flavor, aftertaste, acidity) | 84–99% complete |
| Agtron roast reading | 95% |
| Roast level | 94% |
| Price, converted and inflation-adjusted per pound | 74%, and only from 2010 on |
| Processing method, variety, harvest | 33 reviews: fields the site added recently |
| Priced in a currency other than US dollars | 25% of priced reviews; 1,443 in Taiwanese dollars |
| Ten most-reviewed roasters | 23% of all reviews |

## Known data issues

These limit both halves, so they come first. Several turn into engineering
items below.

1. **Prices only exist from 2010.** Pages before 2009 have no price field.
   About 4% of older reviews mention a price in the review text ("$4.39/pound"),
   which is the only source for that era. The study of real prices over time
   covers about 16 years, not 30.
2. **Scores have risen over time.** The median score was 81 in the late 1990s
   and is 94 now. Any comparison across years, and any model, has to account for
   the year, or it will mostly learn that recent coffees score higher.
3. **Scores fall in a narrow band.** Half of all reviews score from 90 to 94,
   which limits how much any feature can appear to explain.
4. **The score is built from the sub-scores.** Predicting the score from the
   sub-scores recovers the site's formula rather than finding anything. Models
   of what drives the score must leave the sub-scores out.
5. **The sub-score scale changed.** Early reviews used a different scale, and
   about 48 rows per sub-score hold fractional values such as 4.3.
6. **Processing method and variety are mostly in the text.** The structured
   fields cover 33 reviews, but "washed" appears in 35% of reviews, "natural"
   in 25% and "Geisha" in 7%. Some words are ambiguous: "honey" is both a
   processing method and a flavor note.
7. **Espresso and milk drinks are scored differently.** 14% of reviews are
   espressos, and 1,248 carry a "with milk" score. Some analyses should
   separate them or leave them out.
8. **Extreme prices.** The median is $31.50 a pound in January 2026 dollars, but
   176 coffees cost more than $200 a pound and the highest more than $10,000:
   competition lots, sold in small amounts. Use medians and log scales.
9. **Recent months lack inflation data.** The BLS publishes CPI with a lag, and
   October 2025 was never published, so 22% of 2026 prices are unadjusted.
10. **The reviews are not a random sample of the market.** CoffeeReview picks
    what it reviews, roasters send in their coffees, and a few roasters account
    for much of the data. Findings describe reviewed coffees, not all coffee.
11. **Blends.** 8% of reviews name more than one origin country.
12. **A bug:** 2,374 reviews have a `price_baseline_date` but no price. The
    baseline should be empty wherever `price_usd_adj` is empty.

---

## Part 1: Data engineering

The goal is a dataset that someone else could pick up and analyze without
reading the code: validated on every run, documented column by column, and
carrying the features the analysis needs.

### 1.1 Validation (Next)

Checks on the cleaned data that stop the run when they fail, instead of quietly
writing bad data:

- `url` is unique, and the row count never drops between runs.
- Scores, sub-scores and Agtron readings fall in their valid ranges.
- Required fields are present.
- Prices are positive, currencies are ISO codes, and the share of rows with a
  parsed price stays near its usual level.
- Every review date falls in the site's date range.
- A short report of what was checked and what was dropped, such as Agtron typos.

Pandera would express these as a schema. Hand-written checks keep the
dependencies down. Either fits the existing pure `DataFrame -> DataFrame`
style.

### 1.2 Fixes to the current pipeline (Next)

- **Fix the baseline-date bug** (known issue 12), with a test.
- **Count unparsed prices.** 77% of reviews have an `est_price` but 75% end up
  with a parsed price. List the roughly 2% that fail and fix the common
  patterns.
- **Flag the sub-score scale.** Add a column for the scale a review was scored
  on, so the analysis can filter or rescale.
- **Decide how espresso is defined.** `is_espresso` currently matches the title
  or a "with milk" score. Check it against a sample.
- **Recover early prices from the text (Maybe).** Extract the prices older
  reviews mention, flagged as coming from the text rather than the price
  field.

### 1.3 Features from the review text (Next)

The analysis depends on this more than anything else. Use a new module, such as
`coffee/enrich.py`: rule-based and tested, like the roaster matching.

- **Processing method:** washed, natural, honey, anaerobic, wet-hulled, and
  other fermentation styles.
- **Variety:** Geisha/Gesha, Bourbon, Typica, SL28/SL34, Pacamara, Caturra and
  others.
- **Flavor words**, mapped to the categories of the Specialty Coffee
  Association flavor wheel: fruity (berry, citrus, stone fruit, tropical),
  floral, sweet, chocolate, nutty, spice, roasted, fermented, and so on.
- **Origin region below the country:** Yirgacheffe, Guji, Huila, Nyeri,
  Boquete.
- **Other markers:** decaf, blend versus single origin, altitude when stated,
  organic or fair-trade certification, competition lots (Cup of Excellence,
  Best of Panama).

Considerations:

- The 33 reviews with structured processing and variety fields are an answer
  key. Measure the extractor's precision and recall against them, plus a
  hand-labeled sample of older reviews.
- Ambiguous words need context. "Honey" next to "process" is a processing
  method; "honey" in a list of flavors is a flavor.
- Where a field exists, prefer it over the text, and record which source each
  value came from.

### 1.4 Output tables (Later)

- `reviews`: one row per review, with a stable `review_id` such as the URL slug.
- `flavor_notes`: one row per review and flavor word, with its category. A long
  table keeps the vocabulary open-ended and makes "share of reviews mentioning
  X, by year" one query.
- `roasters`: one row per canonical roaster, with location and review count.
- Optionally a DuckDB file holding all three, so the dashboard and ad hoc
  questions can use SQL.

### 1.5 Documentation (Next)

- **Data dictionary:** every column's type, meaning, units, completeness and
  caveats (known issues 1–11). Generated from the schema in 1.1 where possible,
  so it cannot drift from the code.
- **Dataset card:** what the dataset covers, how it was built, its known
  biases, and how to cite it.
- **A shorter README:** the pitch, the main findings once they exist, the
  pipeline diagram and a quickstart. Move the operating instructions, such as
  the roaster review workflow and configuration details, into `docs/`.
- **Dataset versions:** a changelog of what changed between versions of the
  dataset, separate from the code's history.

### 1.6 Automation and operations (Later)

- **Scheduled refresh** with GitHub Actions. Three things stand in the way: the
  saved pages (about 240 MB) live only on this machine, the exchange-rate step
  needs the API key stored as a GitHub secret, and the roaster review queue
  needs a person. One option is a scheduled job that scrapes only new reviews,
  stores the saved pages as a build cache or release asset, and opens a pull
  request with the updated data for you to check.
- **Back up `data/downloaded/`.** It holds the only copy of reviews the site
  has since removed. A compressed archive kept somewhere other than this
  laptop is enough.
- **Run history:** a small log per run of reviews added, changed, failed and
  retired, so changes on the site show up over time.

### 1.7 Geocoding (Later)

Turn roaster locations and origin regions into coordinates for maps in the
dashboard. Possible sources are Nominatim (OpenStreetMap) or the free Map Maker
API. Cache every lookup in a committed file, so each place is requested once.

### 1.8 Engineering polish (Maybe)

- Measure test coverage with `pytest-cov`.
- Property-based tests for the price and quantity parser, using Hypothesis.
- Support Python 3.14: widen `requires-python` and add it to the CI matrix.
- Merge Dependabot's pull requests as they arrive.

---

## Part 2: Data analysis

Two parts, built on the cleaned dataset only and never on the pipeline's
internal files.

### 2A. Descriptive dashboard

What the data shows, without modeling. Most of it can start now on the existing
fields; the processing and variety views wait for 1.3.

**Tools.** A static site on GitHub Pages is free and stays up without a server:

- Evidence.dev: pages written in SQL and Markdown, a good fit with DuckDB.
- Quarto dashboards: Python, close to the existing notebooks.
- Observable Framework: the most flexible, in JavaScript.
- Streamlit: Python and interactive, but it needs a host (Streamlit Community
  Cloud is free).

**Views:**

- **Overview:** number of reviews, years covered, median score and price, and
  the most-reviewed origins and roasters.
- **Scores over time:** the score distribution by year, which shows the
  inflation directly.
- **Prices over time:** nominal against real (inflation-adjusted) price per
  pound; the median with its spread; the share of coffees above $50 and $100 a
  pound by year; prices by roaster country.
- **Origins:** reviews per origin over time, a world map, and the median score
  and price by origin.
- **Roasters:** the most-reviewed roasters, a map of roaster locations, and how
  consistent each roaster's scores are.
- **Roast:** Agtron readings over time, to test whether roasts have grown
  lighter.
- **Processing and variety** (after 1.3): the rise of naturals, anaerobic
  fermentation and Geisha.
- **Explorer:** a filterable table of reviews, each linking to the original.

**Design considerations:**

- Show the number of reviews behind each point, since early years and small
  origins are thin.
- Prefer medians, because of the extreme prices.
- Mark where prices begin (2010) and where inflation data runs out.
- Explain the score inflation next to every chart that compares scores across
  years.

### 2B. Deeper analysis

#### Pricing: what drives price, and how real prices changed

- **Real prices over time, 2010–2026.** Separate two effects: coffees getting
  more expensive, and the mix of reviewed coffees shifting toward expensive
  kinds such as Geisha and competition lots. A hedonic price regression does
  this: model log price per pound on origin, processing, variety, score,
  roaster country and year. The year terms then give a price index adjusted for
  quality and mix.
- **What a point of score costs.** How price rises with score, where the
  increases level off, and which coffees score high for little money (a value
  frontier).
- **Where the roaster is.** Price levels for Taiwanese, US and other roasters,
  and whether converting currencies at the review month's rate is enough.
- **Considerations:** the price is the roaster's estimated retail price at the
  time of the review. Small bags cost more per pound. Use log prices, and check
  that results hold without the most extreme lots.

#### Scores: what drives them

- **The target:** a score that removes the inflation, such as the percentile
  within the review's year, or what is left after modeling the year trend.
  Leave the sub-scores out (known issue 4).
- **Features:** processing method, variety, origin country and region, roast
  level and Agtron reading, price, roaster country, espresso or not, and
  flavor categories.
- **Models:** start with a regularized linear model for readable effects, then
  add the roaster as a random effect, since some roasters score high
  throughout. Compare with gradient-boosted trees and SHAP values to catch
  nonlinear effects.
- **Questions to answer:** does natural processing score higher once origin
  and roast are held constant? Is there a Geisha premium? Do lighter roasts
  score higher, and has that changed over time? Do espressos score differently?
- **The structure of the sub-scores:** which sub-scores move together, which
  matters most to the overall score, and whether that changed across eras.

#### Text analysis

- **How the vocabulary changed:** words that rose or fell over the decades,
  such as "tropical fruit" or "fermented".
- **Words linked to high scores:** compare the vocabulary of high- and
  low-scoring reviews with a method that handles rare words well, such as
  weighted log-odds ratios ("Fightin' Words", Monroe et al., 2008).
- **Predicting the score from the blind assessment:** first TF-IDF with ridge
  regression, whose coefficients can be read directly, then sentence
  embeddings. The reviewer writes the text and gives the score at the same
  tasting, so this shows the vocabulary of praise; it does not show what causes
  a high score.
- **Topics:** NMF, or BERTopic on embeddings, to find recurring themes in the
  reviews.

#### Clustering coffees by flavor profile

- **What to cluster on:** flavor-category profiles from 1.3, embeddings of the
  blind assessment, the sub-scores and the Agtron reading. Try each alone and
  combined, scaled so that no one source dominates.
- **Method:** reduce with UMAP, then cluster with HDBSCAN (see
  `notes/hdbscan-umap.md`). Compare with k-means or Gaussian mixtures as a
  baseline.
- **Stability:** re-cluster bootstrap samples and measure how well the
  clusters agree (adjusted Rand index). Report only clusters that persist.
- **Interpretation:** name each cluster by its most common flavor words, then
  cross-tabulate it against processing method, origin and roast. The
  interesting finding is whether flavor groups follow processing, origin, roast
  or none of these.
- **Use it in the dashboard:** a two-dimensional map of coffees colored by
  cluster, with the review on hover.

#### Statistical care

- Report effect sizes with confidence intervals, not only p-values. Bootstrap
  where the formulas are doubtful.
- Correct for multiple comparisons when testing many origins or words.
- When predicting, split by time (train on the past, test on recent years) and
  keep each roaster's reviews on one side of the split, so that the model
  cannot win by recognizing roasters.
- Describe associations, not causes (known issue 10).

#### The write-up

- A report rendered with Quarto and published on GitHub Pages beside the
  dashboard.
- A findings section at the top of the README, with three or four charts and a
  sentence on each.
- Analysis code that more than one notebook uses moves into tested functions.

---

## A suggested order

1. Validation and pipeline fixes (1.1, 1.2).
2. Features from the review text (1.3).
3. Data dictionary and the shorter README (1.5).
4. Dashboard, first version, on the existing fields (2A). This can run
   alongside step 2.
5. The pricing analysis (2B), which needs only the existing fields plus
   processing and variety.
6. What drives scores (2B).
7. Text analysis and clustering (2B).
8. Output tables, automation and geocoding (1.4, 1.6, 1.7), as the dashboard
   and analysis need them.
