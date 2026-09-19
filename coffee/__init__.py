"""Scrape and analyze CoffeeReview.com data.

The pipeline runs in three steps, each exposed as a console command (see
``[project.scripts]``) and importable here:

* **scrape** — :mod:`sitemap` discovers review URLs, :mod:`fetch` retrieves
  pages, :mod:`parser` turns one page into a record, :mod:`review` combines
  those two for a single review, and :mod:`pipeline` drives the whole run.
* **augment** — :mod:`exchange_rates` fetches historical rates so prices from
  different countries and years can be compared.
* **resolve** — :mod:`roaster_resolution` clusters roaster-name spellings into
  canonical entities.

:mod:`config` holds paths and credentials; :mod:`cli` holds argument parsing.
Data cleaning and analysis live in the project's notebooks.
"""
