# TODO

**Updated**: 9/19/2026

- Review the 18 chain-risk rows in `data/processed/roaster_crosswalk.csv`
  (5 clusters; several are legitimate, e.g. Nescafé/Nestlé USA)
- Work the 50 pairs in `data/processed/roaster_review_queue.csv`
- Coalesce `acidity` / `acidity/structure` during cleaning — the site renamed
  the field across 2017–18, so aggregates on either column alone silently cover
  only part of the corpus
- `coffee/exchange_rates.py` accumulates every rate in memory and writes only at
  the end, so a crash mid-run loses the work and burns quota (1000 req/month).
  Write incrementally and skip dates already present in the output.
- Use `sitemap_lastmod` to re-scrape only changed reviews instead of the whole
  corpus (~200 pages/month rather than ~9,300)
