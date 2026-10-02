---
name: open-data
description: Finds, downloads and decodes free ECMWF real-time forecasts — IFS HRES, IFS ENS, AIFS single and AIFS ENS — from ECMWF Open Data (data.ecmwf.int and its AWS/Google mirrors). Use when a task asks for a current or recent ECMWF forecast, the weather forecast for a place or coordinates, a meteogram or time series at a point, which run is latest or when the next one arrives, which parameters, steps, levels or streams are published openly, how big a download is, Open Data URLs or .index files, or the CC-BY-4.0 attribution ECMWF requires. Default route whenever no CDS, MARS or Polytope credentials are available.
compatibility: scripts/odcatalog.py needs only Python 3 (standard library). scripts/odpoint.py needs uv (PEP 723 inline dependencies — earthkit-data, earthkit-geo, earthkit-meteo, earthkit-utils, ~150 MB on first run, cached) on Linux or macOS. Both need network access to data.ecmwf.int.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.0"
---

# ECMWF Open Data

## Contents
- Scripts — which one to run
- Point forecast workflow
- Facts to get right (models, runs, streams, parameters)
- Dependencies — earthkit first, standard-library fallback
- Credentials and better routes
- Attribution (required)
- Old patterns (pre-50r1 streams)
- References — `references/catalog.md` (paths, products, .index, parameters), `references/fields.md` (generated: every published field with name and units), `references/attribution.md` (full notices, HTML snippet)

Free, keyless, CC-BY-4.0 global forecasts on a 0.25° grid, as GRIB2 files on fixed run schedules.
There is **no per-location API**: a point forecast means downloading global fields and extracting
the nearest gridpoint. The scripts do that with the minimum download.

## Scripts

Run them; don't read them. Paths are relative to this skill's directory; all accept `--help`
and `--json`.

| Need | Run |
|---|---|
| Forecast or meteogram data at a place | `uv run scripts/odpoint.py --lat LAT --lon LON` |
| Latest run, steps, URLs, fields in a run | `python3 scripts/odcatalog.py latest\|steps\|url\|fields` |
| Size before downloading | `python3 scripts/odcatalog.py estimate --param 2t,tp --step 0-240` |
| Raw GRIB of selected fields only | `python3 scripts/odcatalog.py download --param 2t --step 24 -o out.grib2` |
| Decode, plot or process GRIB | the `earthkit` skill |
| **Ensemble spread / uncertainty at a place** | the `polytope` skill: `ptpoint.py --check`, then `ptpoint.py --ensemble` |

Use ECMWF sources only — never substitute a third-party weather API; if no ECMWF route works,
say so.

## Point forecast workflow

```
- [ ] 1. Coordinates: geocode the place (ask the user if ambiguous)
- [ ] 2. Steps: shortest range that answers the question, starting at 0 (0-48 for "tomorrow")
- [ ] 3. Run: uv run scripts/odpoint.py --lat LAT --lon LON --steps 0-48 --json > point.json
- [ ] 4. Check: "series" non-empty and gridpoint distance_km small (<20 km on land)
- [ ] 5. Answer in the user's local time; relay "note" once and the attribution
```

- Output per valid time (UTC): `t2m_C`, `precip_mm` (since the previous step; `null` if unknown),
  `wind_speed_ms`, `wind_dir_deg` (direction the wind blows from), `msl_hPa`, `tcc_pct`.
- Size: 0–240 h ≈ 200 MB, 0–48 h ≈ 40 MB; ~10–20 s per 100 MB. Give the command a timeout of
  several minutes. Don't thin steps to go faster — it ruins meteograms.
- On download errors retry once with `--source google`. The AWS mirror may answer `503 Slow Down`.
- Meteogram image: pass `point.json` to the `earthkit` skill's `ekplot.py meteogram`.

## Facts to get right

- **Models**: `ifs` (default — IFS HRES/ENS), `aifs-single`, `aifs-ens` (ECMWF's machine-learning
  models). Use IFS unless asked; mention AIFS as an option.
- **Runs**: 00/06/12/18 UTC. IFS 00/12z reach 360 h (3-hourly to 144 h, then 6-hourly); IFS
  06/18z reach 144 h. AIFS reaches 360 h in 6 h steps at every run. The scripts pick a run long
  enough for the requested steps.
- **Streams**: `oper` (HRES), `enfo` (ENS, 50 perturbed members), `wave`, `waef`.
- **Availability**: a run appears ~6–8 h after base time; ~2–3 days are kept on data.ecmwf.int.
  Older runs: `--source aws` (archive starts 2023-01-18). Portal limit: 500 simultaneous
  connections.
- **Parameters**: `2t`, `2d`, `tp` (m, accumulated from step 0), `10u`/`10v`, `10fg`, `msl` (Pa),
  `tcc` (0–1), `ptype`, `sf`, `mucape`; pressure levels (`--levtype pl`) `t`, `u`, `v`, `gh`, `r`,
  `q`. Exact list for a run: `odcatalog.py fields --step 24`. Table and file layout:
  `references/catalog.md`.

## Dependencies — earthkit first

Decoding uses earthkit components (≥ 1.0), each for its job: `earthkit-data` (decode),
`earthkit-geo` (nearest point), `earthkit-meteo` (wind), `earthkit-utils` (units). `uv run`
installs only these. Never install the `earthkit` meta-package or `earthkit-data[all]`.

Downloads use parallel HTTP Range requests (8 connections, ~5× faster than earthkit-data's
sequential fetch); `--fetcher earthkit` uses earthkit-data's `ecmwf-open-data` source instead.

Standard-library fallback — only if uv can't be installed, there are no wheels (Windows: use WSL),
installation fails, or the user declines: `odcatalog.py download` still fetches the exact GRIB
fields, but nothing can decode them. Say so and offer
`pip install 'earthkit-data>=1.2' 'earthkit-geo>=1.1' 'earthkit-meteo>=1.2' 'earthkit-utils>=1.0'`.

## Credentials and better routes

Open Data needs none. If the user has better access, say once what it would add:

| Found | Adds |
|---|---|
| `POLYTOPE_USER_KEY`, `~/.polytopeapirc` (or `~/.ecmwfapirc` for member-state users) | server-side point extraction in KB — `polytope` skill |
| `CDSAPI_KEY` or `~/.cdsapirc` | ERA5 history and climate normals — `cds-ads` skill |
| `~/.adsapirc` | CAMS air quality — `cds-ads` skill |
| `ECMWF_API_KEY` or `~/.ecmwfapirc` | MARS archive (licensed) — `mars` skill |

Check presence only; never print key values.

## Attribution

Required wherever the data or anything derived from it is shown:

- Short form (chat answers, figures, map corners): `Data: © <year> ECMWF, CC BY 4.0`, linked to
  https://creativecommons.org/licenses/by/4.0/ where possible.
- Full notice for services, apps, files and READMEs: `references/attribution.md`.
- State modifications ("nearest gridpoint", "converted to °C").

## Old patterns

IFS Cycle 50r1 (13 May 2026) changed the streams. Archived files from before that date (e.g. on
the AWS mirror) use `scda`/`scwv` for 06/18z IFS runs, which then reached only 90 h, and IFS ENS
had a control member `cf`. The scripts map stream names automatically from the run date.
