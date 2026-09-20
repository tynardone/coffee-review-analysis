# TODO

**Updated**: 9/20/2026

- **Make `fetch-exchange-rates` incremental.** Today `fetch_rates`
  (`coffee/exchange_rates.py:84`) fetches every date unconditionally and
  `save_rates` (`coffee/cli.py:131`) writes the whole dict over the existing
  file. Two problems, on a 1000 req/month quota:
  - A re-run costs 323 requests to re-download **immutable** data — historical
    rates for a past date never change. Three re-runs locks out the month.
  - A rate-limited run **destroys the good file**: `fetch_rate` returns `{}` on
    failure (`:81`), those empties enter the dict, and the save overwrites. The
    `failures` warning (`cli.py:128`) prints after the quota is already spent
    and does not stop the write.

  Fix: load what's held, fetch only missing dates, merge rather than replace,
  and refuse to save a result with fewer populated dates than the input. Also
  write incrementally so a crash mid-run keeps what it got.
- Validation at the raw → cleaned boundary: row counts, `url` uniqueness, value
  ranges, nullability
- Review the 18 chain-risk rows in `data/processed/roaster_crosswalk.csv`
  (5 clusters; several are legitimate, e.g. Nescafé/Nestlé USA)
- Work the 50 pairs in `data/processed/roaster_review_queue.csv`
- Coalesce `acidity` / `acidity/structure` during cleaning — the site renamed
  the field across 2017–18, so aggregates on either column alone silently cover
  only part of the corpus
- Confirm `load_review_dates` still matches the raw-layer `"review date"` column
  name after the cleaning-layer move
- Rename `data/processed/` → `data/reference/` (it now holds only roaster
  reference data, which is an input to cleaning rather than a layer)
- Confirm `data/processed/review_dates.csv` is orphaned, then remove it
- Consolidate HTTP clients on one library (`requests` + `aiohttp` → `httpx`) so
  one retry policy serves both call sites. Pure refactor, no behaviour change —
  low priority, and not worth doing piecemeal.
