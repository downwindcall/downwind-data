# Contributing data

You can add or correct an airfield or an aircraft. Please read this first: wrong data can put a pilot in the wrong place, even in a simulator.

## Rules
1. **Use a source.** Circuit side, circuit height, circuit size and speeds must come from an official document (AIP, AIC, VAC chart, or the aircraft's POH). Add it to the file:

```json
"meta": {
  "status": "community",
  "updated": "2026-11-27",
  "change_note": "Training circuit moved to the north side per the new VAC.",
  "sources": [{"title": "EHLE VAC, 2026 edition", "url": "https://example.org/vac.pdf", "date": "2026-11-27"}]
}
```
2. **Small steps.** A single change may not move a circuit measurement by more than 60%. Changes over 25% are flagged for a maintainer.
3. **Plain text only.** No HTML, links in notes, or hidden characters. Notes are at most 300 characters.
4. **One airfield or aircraft per pull request.**
5. **Look at the picture.** The check draws the circuit and attaches it to the run. Make sure the side and size are right.

## What is checked automatically
Allowed fields only; circuit side is left or right; height 400-1500 ft AGL; downwind offset 0.3-1.5 NM; base turn 0.1-1.5 NM; frequencies 118.000-136.975; runways must exist at the airport (OurAirports); aircraft speed bands must be sane (low end above the stall speed, base no faster than downwind, flaps the aircraft really has). Run it yourself:

    python validator.py profiles --mode submit --base <copy-of-main>/profiles --data data
    python tests/test_validator.py
    python tools/preview_circuit.py profiles/airports/XXXX.json --out previews

## Your own settings
Changes you make in the app's Settings stay on your PC, are labelled "Your own settings", and are never shared.

## Licence of your contribution
Data you contribute is shared under the data licence of this repository.

## For the maintainer
After merging data changes, run `python tools/make_index.py` and commit `profiles/index.json`. The app's updater only uses files listed in that index with a matching hash, so an out-of-date index means new data is not used yet (it never causes bad data to be used). The "Validate data" run on `main` turns red when the index is stale.
