# Airport profiles

One file per airport in `profiles/airports/`. **The file name is the ICAO code**: `EHLE.json`, `EHAM.json`, `LEGR.json`.
Any airport known to the sim works without a file (generic defaults from `defaults.json`); add a file only to describe how that field is flown.

1. Copy `airports/_template.json` to `airports/<ICAO>.json`.
2. Delete every key you don't need. Missing keys fall back to `defaults.json`.
3. Restart is not needed: the coach re-reads the file every second. Saving in Settings also writes this file.

The Settings dialog (circuit height, side, PAPI, glide) edits the same file, and so do the "Sync PAPI" buttons. Nothing about a specific airport is stored in the code.

## Measuring the circuit from a chart (VAC / visual approach chart)
The chart has no scale, so scale it from the runway length:
1. Mark both runway ends; their pixel distance = runway length (m).
2. Downwind offset = distance from the runway centreline to the downwind leg -> `circuit.offset_nm`.
3. The turn from downwind begins where the leg leaves the straight line; its distance before the approach threshold -> `circuit.base_along_nm`. The upwind-to-crosswind turn is placed the same distance beyond the departure end unless you set `circuit.crosswind_along_nm`.
4. The turn radius (half the loop width) -> `circuit.final_side_nm`.
Accuracy is about +-10%: fly a few circuits and adjust.


## Aircraft: shipped profiles vs. your own edits

`profiles/aircraft/<id>.json` are the shipped profiles. Do not edit them to tune an aircraft: use Settings > Aircraft in the app.
Your edits (speed bands, flap positions, rotate speed) are saved separately in `profiles/aircraft/user/<id>.json`, win over the shipped values,
and can be discarded with "Back to shipped values". Profiles with `"_verified": false` have not been checked against the POH; `_sources` and
`_confidence` say where the numbers came from.

## Data checks, limits and trust labels

Every airport and aircraft file is checked by `validator.py` when it is loaded, when it is saved from the app, and on every pull request (CI).

Run it yourself: `python validator.py profiles --mode local --data data`

**Hard limits (errors, the value is rejected):** circuit height 400–1500 ft AGL; circuit offset 0.3–1.5 nm; base/final/crosswind distances 0.1–1.5 nm; glide angle 2.0–5.5°; runway idents and frequencies must be well-formed; plain text only (no markup).

**Warnings (a human must look):** circuit side flipped vs the current file ("CIRCUIT SIDE CHANGE"), geometry changed by more than 25 %, circuit height changed by more than 30 %, runway or ICAO not found in OurAirports.

**`meta` block:** `{"status": "community"|"local", "updated": "YYYY-MM-DD", "sources": [{"url": "https://…", "date": "YYYY-MM-DD"}]}`. Changing safety fields in a pull request requires at least one source (AIP/VAC chart, official publication). `status` can never be `verified` in a file.

**Trust labels shown in the app:** *Verified* = content hash is listed in `profiles/verified.json` (maintainers only, via `tools/mark_verified.py`); *Community* = passes checks, not reviewed; *Local* = edited on this PC. Any edit to a verified file drops it back to Community until re-reviewed.

Preview a circuit change before merging: `python tools/preview_circuit.py --base <old dir> --out preview/` (grey dashed = current, blue = proposed).
