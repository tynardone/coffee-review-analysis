# TODO

**Updated**: 9/21/2026

- Validation at the raw → cleaned boundary: row counts, `url` uniqueness, value
  ranges, nullability. `check_raw_schema` covers column names; nothing yet
  checks the data itself.
- Geocode roaster and origin locations to coordinates, possibly with the free
  API from [Map Maker](https://maps.co/). Nothing calls it yet; it was listed
  as a planned data source in the README.
- Consolidate HTTP clients on one library (`requests` + `aiohttp` → `httpx`) so
  one retry policy serves both call sites. Pure refactor, no behaviour change —
  low priority, and not worth doing piecemeal.
