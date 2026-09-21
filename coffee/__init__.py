"""Scrape, clean, and store CoffeeReview.com data.

Collection and cleaning are automated; analysis is not. ``refresh-data`` runs
the whole pipeline in dependency order, and each step is also a command of its
own (see ``[project.scripts]``):

1. **scrape** (``scrape-reviews``) — :mod:`sitemap` discovers review URLs,
   :mod:`fetch` retrieves pages, :mod:`parser` turns one page into a record,
   :mod:`review` combines those two, and :mod:`pipeline` drives the run.
   Incremental by default: only new or changed URLs are fetched.
2. **resolve** (``resolve-roasters``) — :mod:`roaster_resolution` clusters
   roaster-name spellings into a canonical crosswalk, combining automatic
   grouping with recorded manual verdicts.
3. **augment** (``fetch-exchange-rates``) — :mod:`exchange_rates` fetches
   historical rates for the review months in the raw scrape.
4. **clean** (``clean-reviews``) — :mod:`clean` turns raw rows into the cleaned
   layer, applying :mod:`enrich` to put prices in comparable money when
   exchange rates and CPI are available.

That produces two layers: ``data/raw/reviews.csv`` as scraped, and
``data/clean/reviews.csv`` ready for analysis. Analysis itself lives in the
notebooks; nothing here produces charts or aggregates.

:mod:`storage` sits between the pipeline and where reviews are kept, so the CSV
corpus can be replaced by a database without changing the scrape.
:mod:`config` holds paths and credentials; :mod:`cli` holds argument parsing.
"""
