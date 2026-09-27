"""Scrape, clean, and store CoffeeReview.com data.

Collection and cleaning are automated; analysis is not. ``refresh-data`` runs
the whole pipeline in dependency order, and each step is also a command of its
own (see ``[project.scripts]``):

1. **scrape** (``scrape-reviews``) — :mod:`scrape` drives the step:
   :mod:`sitemap` discovers review URLs, :mod:`http_client` retrieves pages,
   and :mod:`page_store` saves them. Incremental by default: only new or
   changed URLs are fetched.
2. **parse** (``parse-reviews``) — :mod:`parse` drives the step:
   :mod:`review_page` turns each saved page into a record, and
   :mod:`review_store` writes the records to ``reviews.csv``. No network, so a
   parser fix is a re-parse rather than a re-download.
3. **resolve** (``resolve-roasters``) — :mod:`roasters` clusters
   roaster-name spellings into a canonical crosswalk, combining automatic
   grouping with recorded manual verdicts.
4. **augment** (``fetch-exchange-rates``, ``fetch-cpi``) —
   :mod:`exchange_rates` fetches historical rates for the review months in the
   parsed reviews, and :mod:`cpi` fetches the BLS price index. Both merge into what
   is already held rather than replacing it.
5. **clean** (``clean-reviews``) — :mod:`clean` turns parsed rows into the
   cleaned dataset, applying :mod:`prices` to put prices in comparable money when
   exchange rates and CPI are available.

The review data moves through three folders under ``data/``, in pipeline
order: ``downloaded/`` holds the pages as served, ``parsed/reviews.csv`` one row
per review, and ``cleaned/reviews.parquet`` the typed, priced table the
notebooks read. The parsed CSV stays plain text because it is the committed
copy; the cleaned table is Parquet because it is regenerated on demand, read
only by code, and Parquet keeps its types. Analysis itself lives in the
notebooks; nothing here produces charts or aggregates.

:mod:`review_store` sits between the parse and where reviews are kept, so the
CSV corpus can be replaced by a database without changing the parse.
:mod:`settings` holds paths and reads secrets; :mod:`cli` holds argument parsing.
"""
