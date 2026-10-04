---
name: open-data
description: Finds, downloads and decodes free ECMWF real-time forecasts — IFS HRES, IFS ENS, AIFS single and AIFS ENS — from ECMWF Open Data (data.ecmwf.int and its AWS/Google mirrors). Use when a task asks for a current or recent ECMWF forecast, the weather forecast for a place or coordinates, a meteogram or time series at a point, which run is latest or when the next one arrives, which parameters, steps, levels or streams are published openly, how big a download is, Open Data URLs or .index files, or the CC-BY-4.0 attribution ECMWF requires. For web maps or dashboards use the opencharts-wms skill; when the user has or mentions an ECMWF account, use the polytope skill for point forecasts. Default route whenever no CDS, MARS or Polytope credentials are available.
compatibility: scripts/odcatalog.py needs only Python 3 (standard library). scripts/odpoint.py needs uv (PEP 723 inline dependencies — earthkit-data, earthkit-geo, earthkit-meteo, earthkit-utils, ~150 MB on first run, cached) on Linux or macOS. Both need network access to data.ecmwf.int.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.8"
---

<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# ECMWF Open Data

## Contents
- Scripts (line 33)
- Point forecast workflow (line 53)
- Facts to get right (line 74)
- Dependencies — earthkit first (line 90)
- Credentials and better routes (line 104)
- Attribution (line 119)
- When blocked (line 128)
- Old patterns (line 145)
- References — `references/catalog.md` (paths, products, .index, parameters), `references/fields.md` (generated: every published field with name and units), `references/attribution.md` (full notices, HTML snippet)

Free, keyless, CC-BY-4.0 global forecasts on a 0.25° grid, as GRIB2 files on fixed run schedules.
There is **no per-location API**: a point forecast means downloading global fields and extracting
the nearest gridpoint. The scripts do that with the minimum download.

## Scripts

Run them; don't read them. Run them from the user's working directory and write
files there, never inside the skill directory. Paths are relative to this skill's directory; all accept `--help`
and `--json`.

| Need | Run |
|---|---|
| Forecast or meteogram data at a place | `uv run scripts/odpoint.py --lat LAT --lon LON` |
| Latest run, steps, URLs, fields in a run | `python3 scripts/odcatalog.py latest\|steps\|url\|fields` |
| Size before downloading | `python3 scripts/odcatalog.py estimate --param 2t,tp --step 0-240` |
| Raw GRIB of selected fields only | `python3 scripts/odcatalog.py download --param 2t --step 24 -o out.grib2` |
| Decode, plot or process GRIB | the `earthkit` skill |
| Web map or dashboard (layers, time slider, click → meteogram) | the `opencharts-wms` skill: `webmap.py create` |
| Forecast at a place **and the user mentions an ECMWF account or key** | the `polytope` skill (`ptpoint.py --check`, then `ptpoint.py`) |
| **Ensemble spread / uncertainty at a place** | the `polytope` skill: `ptpoint.py --check`, then `ptpoint.py --ensemble` |

Use ECMWF sources only — never substitute a third-party weather API; if no ECMWF route works,
say so.

## Point forecast workflow

```
- [ ] 1. Coordinates: geocode the place (ask the user if ambiguous)
- [ ] 2. Window: --next-hours N for "the next N hours/days" (or --steps 0-END from the run start)
- [ ] 3. Run: uv run scripts/odpoint.py --lat LAT --lon LON --next-hours 48 --tz ZONE --json > point.json
- [ ] 4. Check: "series" non-empty and gridpoint distance_km small (<20 km on land)
- [ ] 5. Answer in the user's local time; relay "note" once and the attribution
```

- `--next-hours N` returns exactly the next N hours (runs start up to ~13 h before now, so
  `--steps 0-24` would cover less); `--tz Europe/Lisbon` adds `local_time` to every row;
  `--from-now` drops past rows; `--daily` adds per-local-day high, low and precipitation — use these
  instead of converting or aggregating yourself.
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

Check presence only; never print key values. If a route would clearly help and its key is
missing, mention it once and offer step-by-step instructions — the owning skill prints them
(`cds.py setup`, `mars.py setup`, `ptpoint.py --setup`, `destine.py setup`).

## Attribution

Required wherever the data or anything derived from it is shown:

- Short form (chat answers, figures, map corners): `Data: © <year> ECMWF, CC BY 4.0`, linked to
  https://creativecommons.org/licenses/by/4.0/ where possible.
- Full notice for services, apps, files and READMEs: `references/attribution.md`.
- State modifications ("nearest gridpoint", "converted to °C").

## When blocked

Scripts print a **BLOCKED** report (what blocks, why, numbered steps for the user, and what
the agent should do) whenever a legal or technical barrier stops access. Relay the user's
steps in full and follow the agent instruction; never work around a barrier with another
provider's data, and never ask for passwords or keys in chat.

If a service can't be reached, the report includes what ECMWF's own status service says
(`scripts/ecmwf_status.py <service>` where present; https://status.ecmwf.int). Say whether ECMWF
reports an outage or maintenance, or that the problem is likely local (network, proxy).

| Barrier | Report |
|---|---|
| earthkit not installable (no uv, Windows) | `odpoint.py`: install steps; `odcatalog.py download` still fetches GRIB |
| no run reachable (network, proxy, outage) | retry `--source aws`/`google`, then the network steps |
| better data needs a key | offer `cds.py setup`, `ptpoint.py --setup`, `mars.py setup` |

## Old patterns

IFS Cycle 50r1 (13 May 2026) changed the streams. Archived files from before that date (e.g. on
the AWS mirror) use `scda`/`scwv` for 06/18z IFS runs, which then reached only 90 h, and IFS ENS
had a control member `cf`. The scripts map stream names automatically from the run date.
