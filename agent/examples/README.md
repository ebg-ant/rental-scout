# Example payloads

Made-up listings, valid against `configs/nyc.example.yaml`. The tests feed these files through the
real commands, so they cannot drift from what the scout accepts.

- `cards.json`: hand in to `python3 -m scout ingest`
- `enrich.json`: hand in to `python3 -m scout enrich`. Shows a concession and an honest unknown.
- `score.json`: hand in to `python3 -m scout score`. Shows a listing with no address: two criteria
  scored `null`, two with required evidence marked unavailable.
