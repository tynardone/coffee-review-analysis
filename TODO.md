# TODO

**Updated**: 9/20/2026

- Decide where the enriched layer lives. `coffee/enrich.py` has no command and
  no file on disk; the notebooks call it in memory. Either give it an
  `enrich-reviews` command writing `data/enriched/reviews.csv`, or leave it as
  a library step the analysis applies.
- Validation at the raw → cleaned boundary: row counts, `url` uniqueness, value
  ranges, nullability
- Review the 18 chain-risk rows in `data/processed/roaster_crosswalk.csv`
  (5 clusters; several are legitimate, e.g. Nescafé/Nestlé USA)
- Work the 50 pairs in `data/processed/roaster_review_queue.csv`
- Coalesce `acidity` / `acidity/structure` during cleaning — the site renamed
  the field across 2017–18, so aggregates on either column alone silently cover
  only part of the corpus
- Rename `data/processed/` → `data/reference/` (it now holds only roaster
  reference data, which is an input to cleaning rather than a layer)
- Confirm `data/processed/review_dates.csv` is orphaned, then remove it
- Consolidate HTTP clients on one library (`requests` + `aiohttp` → `httpx`) so
  one retry policy serves both call sites. Pure refactor, no behaviour change —
  low priority, and not worth doing piecemeal.
