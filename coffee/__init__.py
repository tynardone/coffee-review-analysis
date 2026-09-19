"""Scrape and analyze CoffeeReview.com data.

The pipeline runs in three steps, each exposed as a console command (see
``[project.scripts]``) and importable here:

* **scrape** — :mod:`review_urls` discovers review URLs from the site's
  sitemaps, :mod:`fetch` retrieves them, :mod:`parser` turns each page into a
  record, and :mod:`scrape` drives the whole run.
* **augment** — :mod:`exchange_rates` fetches historical rates so prices from
  different countries and years can be compared.
* **resolve** — :mod:`roaster_resolution` clusters roaster-name spellings into
  canonical entities.

:mod:`config` holds paths and credentials; :mod:`cli` holds argument parsing.
Data cleaning and analysis live in the project's notebooks.
"""
