"""Scrape, clean, and store CoffeeReview.com data.

The pipeline runs in four steps, each exposed as a console command (see
``[project.scripts]``) and importable here:

* **scrape** (``scrape-reviews``) — :mod:`sitemap` discovers review URLs,
  :mod:`fetch` retrieves pages, :mod:`parser` turns one page into a record,
  :mod:`review` combines those two for a single review, and :mod:`pipeline`
  drives the run. Incremental by default: only new or changed URLs are fetched.
* **augment** (``fetch-exchange-rates``) — :mod:`exchange_rates` fetches
  historical rates for the review months the cleaned layer holds.
* **resolve** (``resolve-roasters``) — :mod:`roaster_resolution` clusters
  roaster-name spellings into a canonical crosswalk, combining automatic
  grouping with recorded manual verdicts.
* **clean** (``clean-reviews``) — :mod:`clean` turns raw scraped rows into the
  cleaned layer: parsed types, parsed prices and quantities, resolved origins
  and roaster locations. Depends on the raw scrape and nothing else.

:mod:`enrich` is the step after that, putting prices in comparable money using
exchange rates and CPI. It is kept separate because it needs reference data the
reviews do not carry, and because the cleaned layer must be buildable without
it. It has no command and no layer on disk yet; the notebooks apply it in
memory.

This produces two data layers: ``data/raw/reviews.csv`` as scraped, and
``data/clean/reviews.csv`` ready for analysis. :mod:`storage` sits between the
pipeline and where reviews are kept, so the CSV corpus can be replaced by a
database without changing the scrape.

:mod:`config` holds paths and credentials; :mod:`cli` holds argument parsing.
The notebooks consume the cleaned layer rather than producing it.
"""
