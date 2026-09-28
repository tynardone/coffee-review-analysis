# TODO

**Updated**: 9/27/2026

- Validation at the parsed → cleaned boundary: row counts, `url` uniqueness, value
  ranges, nullability. `check_raw_schema` covers column names; nothing yet
  checks the data itself.
- Geocode roaster and origin locations to coordinates, possibly with the free
  API from [Map Maker](https://maps.co/). Nothing calls it yet; it was listed
  as a planned data source in the README.
