# TODO

**Updated**: 9/18/2026

- Review the 18 chain-risk rows in `data/processed/roaster_crosswalk.csv`
  (5 clusters; several are legitimate, e.g. Nescafé/Nestlé USA)
- Work the 50 pairs in `data/processed/roaster_review_queue.csv`
- Convert `resolve_roasters.py` into a function within the library
- Coalesce `acidity` / `acidity/structure` during cleaning — the site renamed
  the field across 2017–18, so aggregates on either column alone silently cover
  only part of the corpus
- `scripts/openex.py`: stale default input path, and it writes nothing until the
  whole run finishes (costly against a 1000 req/month quota)
