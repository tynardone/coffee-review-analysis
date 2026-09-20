"""Scrape, clean, and store CoffeeReview.com data.

The pipeline runs in four steps, each exposed as a console command (see
``[project.scripts]``) and importable here:

* **scrape** (``scrape-reviews``) — :mod:`sitemap` discovers review URLs,
  :mod:`fetch` retrieves pages, :mod:`parser` turns one page into a record,
  :mod:`review` combines those two for a single review, and :mod:`pipeline`
  drives the run. Incremental by default: only new or changed URLs are fetched.
* **augment** (``fetch-exchange-rates``) — :mod:`exchange_rates` fetches
  historical rates so prices from different countries and years can be
  compared.
* **resolve** (``resolve-roasters``) — :mod:`roaster_resolution` clusters
  roaster-name spellings into a canonical crosswalk, combining automatic
  grouping with recorded manual verdicts.
* **clean** (``clean-reviews``) — :mod:`clean` turns raw scraped rows into the
  cleaned layer: parsed types, USD prices adjusted for inflation, resolved
  origins and roaster locations.

The two data layers this produces are ``data/raw/reviews.csv`` (what the site
said) and ``data/clean/reviews.csv`` (what analysis can use). :mod:`storage`
holds the seam between the pipeline and where reviews live, so the CSV corpus
can later become a database without touching the scrape.

:mod:`config` holds paths and credentials; :mod:`cli` holds argument parsing.
The notebooks consume the cleaned layer; they no longer produce it.
"""
