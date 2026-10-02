---
name: open-data
description: Find, download and decode free ECMWF real-time forecasts — IFS HRES, IFS ENS, AIFS single and AIFS ENS — from ECMWF Open Data (data.ecmwf.int and its AWS/Google mirrors). Use when a task asks for a current or recent ECMWF forecast, the weather forecast for a place or coordinates, a meteogram or time series at a point, which run is latest or when the next one arrives, which parameters, steps, levels or streams are published openly, how big a download is, Open Data URLs or .index files, or the CC-BY-4.0 attribution ECMWF requires. This is the default route whenever no CDS, MARS or Polytope credentials are available.
compatibility: Skill instructions are provider-neutral. scripts/odcatalog.py needs only Python 3 (standard library). scripts/odpoint.py needs uv (PEP 723 inline dependencies — earthkit-data, earthkit-geo, earthkit-meteo, earthkit-utils, ~150 MB on first run, cached) on Linux or macOS. Both need network access to data.ecmwf.int.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.0"
---

# ECMWF Open Data

Free, keyless, CC-BY-4.0 global forecasts on a 0.25° grid, as GRIB2 files on fixed run schedules.
There is **no per-location JSON API** — for a point you download global fields and extract the
nearest gridpoint. The bundled scripts do that with the minimum download.

## Which script

| Need | Run |
|---|---|
| Forecast / meteogram at a place | `uv run scripts/odpoint.py --lat LAT --lon LON` |
| Latest run, steps, URLs, available fields | `python3 scripts/odcatalog.py latest\|steps\|url\|fields` |
| Size before downloading | `python3 scripts/odcatalog.py estimate --param … --step …` or `odpoint.py --estimate-only` |
| Raw GRIB of selected fields only | `python3 scripts/odcatalog.py download --param … --step … -o out.grib2` |
| Decode / plot / process a GRIB file | the `earthkit` skill |

Paths are relative to this skill's directory. Every script supports `--help` and `--json`.

## Point forecast (the common case)

```bash
uv run scripts/odpoint.py --lat 38.72 --lon -9.14                  # 10 days, latest IFS run
uv run scripts/odpoint.py --lat 38.72 --lon -9.14 --steps 0-48 --json
uv run scripts/odpoint.py --lat 38.72 --lon -9.14 --model aifs-single
```

- Geocode place names yourself (you know coordinates of well-known places; otherwise ask the user).
- Output: nearest gridpoint and its distance, then per valid time (UTC): 2 m temperature °C,
  precipitation mm **since the previous step**, 10 m wind speed m/s and direction (from), MSLP hPa,
  total cloud %. Convert times to the user's local time when answering.
- Default 0–240 h on IFS ≈ 200 MB of downloads; use a shorter `--steps` when the question allows
  (`0-48` for "tomorrow" ≈ 40 MB). Check with `--estimate-only` first if bandwidth matters.
- **Downloads take time — roughly 10–20 s per 100 MB** with the default parallel fetcher (8
  connections). Give the command a timeout of several minutes; don't thin steps to 12-hourly just
  to go faster (it ruins meteograms). Always start step ranges at `0` so precipitation intervals
  are complete (a first interval that doesn't start at 0 is reported as null).
- To plot a meteogram: save `--json` output to a file, then use the `earthkit` skill's
  `ekplot.py meteogram`.
- If `data.ecmwf.int` is slow or refuses connections, retry once with `--source google`; the AWS
  mirror may answer `503 Slow Down` under load.
- Always relay the script's `note` (what credentials would improve) once, briefly, and the
  attribution.

## Facts to get right

- **Models**: `ifs` (default — IFS HRES/ENS, physics-based), `aifs-single`, `aifs-ens` (ECMWF's
  machine-learning models). Default to IFS unless the user asks; mention AIFS as an option.
- **Runs**: 00, 06, 12, 18 UTC. IFS 00/12z run to 360 h (3-hourly to 144 h, then 6-hourly);
  IFS 06/18z run to 144 h. AIFS runs to 360 h in 6 h steps at every run. A 10-day request
  therefore needs a 00z/12z IFS run — the scripts pick it automatically.
- **Streams**: `oper` (HRES), `enfo` (ENS, 50 perturbed members), `wave`, `waef`. Since IFS Cycle
  50r1 (13 May 2026) there is **no `scda`/`scwv`** and no ENS control (`cf`) for IFS; older archived
  files on mirrors still use them.
- **Availability**: a run appears ~6–8 h after its base time and is kept ~2–3 days on
  data.ecmwf.int. Older runs: `--source aws` (archive since 2023-01-18). Portal limit: 500
  simultaneous connections — use `--source aws|google` for bulk work.
- **Parameters** (short names): `2t` 2 m temperature (K), `2d` dewpoint (K), `tp` total
  precipitation (m, accumulated from step 0), `10u`/`10v` wind (m/s), `10fg` gusts, `msl` (Pa),
  `tcc` (0–1), `sp`, `ssrd`, `mucape`, `ptype`, `sf`, `sd`. Pressure levels (`--levtype pl`): `t`,
  `u`, `v`, `gh`, `r`, `q`, `w`, `vo`, `d`. List exactly what a run has with
  `odcatalog.py fields --step 24`. Details: `references/catalog.md`.
- **File layout**: `{root}/YYYYMMDD/HHz/{model}/0p25/{stream}/YYYYMMDDHH0000-{step}h-{stream}-{type}.grib2`
  plus a JSON-lines `.index` (one line per field with `_offset`/`_length`) enabling HTTP Range
  downloads of single fields. See `references/catalog.md`.

## Dependencies — earthkit first

Decoding and point extraction use earthkit components (≥ 1.0), each for its job: `earthkit-data`
(fetch/decode), `earthkit-geo` (nearest point), `earthkit-meteo` (wind), `earthkit-utils` (units).
`uv run` installs only these. **Never** install the `earthkit` meta-package or
`earthkit-data[all]` for this.

Fall back to the standard library only if uv is unavailable and cannot be installed, the platform
has no wheels (Windows — use WSL), installation fails, or the user declines. Then
`odcatalog.py download` still fetches the exact GRIB fields, but nothing can decode them — say so,
and offer `pip install 'earthkit-data[ecmwf-opendata]>=1.2' 'earthkit-geo>=1.1'
'earthkit-meteo>=1.2' 'earthkit-utils>=1.0'`.

## Credentials and better routes

Open Data needs none. Before heavy downloads, check whether the user has better access and say
what it would add — briefly, once:

| Found | Means |
|---|---|
| `POLYTOPE_USER_KEY` or `~/.polytopeapirc` (or `~/.ecmwfapirc` for member-state users) | Point/area extraction server-side in KB — `polytope` skill |
| `CDSAPI_KEY` or `~/.cdsapirc` | ERA5 history and climate normals — `cds-ads` skill |
| `~/.adsapirc` | CAMS air quality — `cds-ads` skill |
| `ECMWF_API_KEY` or `~/.ecmwfapirc` | MARS archive (licensed) — `mars` skill |

Never print key values.

## Attribution is required

ECMWF Open Data is CC-BY-4.0. Wherever the data or anything derived from it is shown:

- Short (UI, chat answers, map corners): `Data: © <year> ECMWF, CC BY 4.0` linked to
  https://creativecommons.org/licenses/by/4.0/
- Full notice for services, apps, files and READMEs — `references/attribution.md`.
- State if the data was modified (e.g. "interpolated to a point", "unit-converted").
