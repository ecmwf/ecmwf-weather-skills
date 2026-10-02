# ECMWF Weather Skill — Plan

Status: **in progress** — M1–M4 done; M5 (`mars`) next.
Model: [vaisala-xweather/xweather-agent-skills](https://github.com/vaisala-xweather/xweather-agent-skills).
Research: `docs/research/earthkit-components.md`, `docs/research/open-questions.md`.

## 0. Research step (repeat before every minor release)

Before writing or updating a skill:

1. **Inventory earthkit** — list every `earthkit-*` package on PyPI and in `github.com/ecmwf`,
   record latest version, purpose, deps and download size; keep only **≥ 1.0**; map each needed
   function to one component. Output: `docs/research/earthkit-components.md`.
2. **Verify live services** — Open Data layout/streams, WMS capabilities, OpenCharts API, Polytope
   access, client env vars, licences. Output: `docs/research/open-questions.md`.
3. Update this plan and the affected `SKILL.md` / `references/` with any drift.

Docs frequently lag the services (e.g. IFS 50r1 removed `scda`); trust live checks.

## 1. Goal

An installable [Agent Skills](https://agentskills.io) plugin that makes any coding agent fluent in
ECMWF data: which service to use, how to request, decode, process and map it, and what attribution
to show — for developers, end users and scientists.

## 2. Decisions

| Topic | Decision |
|---|---|
| Ownership | ECMWF-affiliated, `github.com/ecmwf/ecmwf-weather-skill`, Apache-2.0 |
| Plugin / marketplace | `ecmwf-weather` (permanent) @ `ecmwf` |
| Clients | Claude Code, Codex/ChatGPT, generic Agent Skills |
| Access routes | Open Data, CDS/ERA5, ADS/CAMS, MARS, OpenCharts/WMS, Polytope |
| Skills | 6, sliced by access route |
| Default model | IFS HRES; mention AIFS |
| **Tooling priority** | **earthkit first** (≥ 1.0 components, task-scoped); stdlib fallback only when earthkit can't be installed (e.g. Windows, no network to PyPI, no uv) or the user declines |
| Credentials | Env vars + native rc files; missing → Open Data + note on what a key adds |
| Point forecasts | Polytope if credentials, else Open Data + earthkit-geo nearest point |
| Script reports | Estimated size, freshness, licence/attribution |
| MCP | Later (`TODO.md`) |
| Generated refs | Open Data contents, WMS layers, parameter DB, library versions |
| Later | CDS/ADS catalog generation, Destination Earth |

## 3. The six skills

| Skill | Covers | Primary earthkit component |
|---|---|---|
| `open-data` | IFS/AIFS Open Data: runs, streams, steps, params, mirrors, latest-run, point forecast | `earthkit-data[ecmwf-opendata]`, `earthkit-geo` |
| `cds-ads` | ERA5/ERA5-Land, seasonal, CAMS | `earthkit-data[cds]`, `earthkit-transforms[all]` |
| `mars` | MARS requests via Web API | `earthkit-data[mars]` |
| `polytope` | Feature extraction; efficient point/area path | `earthkit-data[polytope]` |
| `opencharts-wms` | WMS layers, OpenCharts API, web maps | none (URLs; stdlib) — `earthkit-plots` for server-side images |
| `earthkit` | Decode, select, derive, regrid, aggregate, plot | all eligible components |

Each skill is self-contained: credential detection, Open Data fallback note, attribution.

## 4. Cross-cutting behaviour

### 4.1 earthkit-first dependency policy

1. **Use earthkit components for their assigned purpose** (map in
   `docs/research/earthkit-components.md`):

   | Purpose | Component |
   |---|---|
   | fetch / read / select / convert | `earthkit-data` (+ extra per source) |
   | nearest point, regrid, country polygons | `earthkit-geo` |
   | derived quantities | `earthkit-meteo` (+ `thermofeel` for thermal indices) |
   | unit conversion | `earthkit-utils` |
   | temporal/spatial aggregation | `earthkit-transforms[all]` |
   | maps, time series | `earthkit-plots` |

2. **Never install the `earthkit` meta-package** or `earthkit-data[all]`. Install only the bundle a
   task needs (fetch ≈ 64 MB, derive ≈ 18 MB, plot ≈ 159 MB). Scripts declare exactly their bundle in
   PEP 723 metadata; `uv run` caches it.
3. **Pin `>=1`** on every earthkit component (prevents silent fallback to 0.x on Windows).
4. **Excluded:** `earthkit-regrid` (deprecated), `earthkit-time`, `earthkit-climate`,
   `earthkit-workflows`, meta `earthkit`.
5. **Measured exception — Open Data fetching.** earthkit-data's `ecmwf-open-data` source
   downloads byte ranges sequentially (~1–2 MB/s). `odpoint.py` defaults to `odcatalog.py`'s
   parallel Range downloader (8 connections, ~10 MB/s, 5× faster, retries; far below the
   500-connection portal limit) and then **decodes with earthkit-data**. `--fetcher earthkit` keeps
   the pure-earthkit path. Revisit if earthkit-data gains parallel downloads (TODO).
6. **Fallback to stdlib** when: uv/pip unavailable, platform has no wheels (Windows), install fails,
   or the user declines installs. Stdlib fallback can still list runs, read `.index`, Range-download
   single GRIB fields and estimate sizes — but cannot decode GRIB. Tell the user what's lost.

### 4.2 Credentials and degradation

| Service | Env | File |
|---|---|---|
| CDS | `CDSAPI_URL`, `CDSAPI_KEY`, `CDSAPI_RC` | `~/.cdsapirc` |
| ADS | — (earthkit) | `~/.adsapirc` |
| MARS | `ECMWF_API_URL` + `ECMWF_API_KEY` + `ECMWF_API_EMAIL` | `~/.ecmwfapirc` |
| Polytope | `POLYTOPE_USER_KEY`, `POLYTOPE_USER_EMAIL`, `POLYTOPE_ADDRESS` | `~/.polytopeapirc`, falls back to `~/.ecmwfapirc` |
| WMS non-public | `ECMWF_API_KEY` as token | `~/.ecmwfapirc` |
| Open Data / OpenCharts / public WMS | none | none |

Always pre-check credentials before calling earthkit (missing creds trigger interactive prompts).
Never print secrets.

### 4.3 Script report block

Request (redacted) · estimated size (`--estimate-only`) · freshness (run, age, next run) ·
licence + attribution.

### 4.4 Attribution (best guess — see research §1)

- UI short form: `Data: © <year> ECMWF, CC BY 4.0` linking to the full notice.
- Full notice for services: "This service is based on data and products of the European Centre for
  Medium-Range Weather Forecasts (ECMWF)…" + CC BY 4.0 + disclaimer.
- Copernicus: "Generated using Copernicus Climate Change Service / Atmosphere Monitoring Service
  information <year>. Neither the European Commission nor ECMWF is responsible…" + dataset DOI.
- MARS: `© ECMWF` + disclaimer + redistribution warning.

### 4.5 Open Data facts the skills must encode

Post-50r1 (13 May 2026): no `scda`/`scwv`; 06/18z `oper` to 144 h; ENS 50 `pf`, no `cf`.
AIFS 0–360 h by 6 at all runs. Retention ~2–3 days on data.ecmwf.int, AWS mirror since 2023.
500-connection limit. `.index` enables single-field Range downloads.

## 5. Scripts (M1 scope marked ★)

| Script | Skill | Kind | Purpose |
|---|---|---|---|
| ★ `odcatalog.py` | open-data | stdlib | Latest run, URLs, steps per model/stream/run, `.index` parsing, size estimate, freshness, attribution |
| ★ `odpoint.py` | open-data | earthkit (data+geo+utils) | Point forecast → JSON/CSV; fallback message if earthkit unavailable |
| ★ `ekinspect.py` | earthkit | earthkit-data | Summarise a GRIB/NetCDF file (params, levels, steps, times, grid) |
| ★ `ekplot.py` | earthkit | earthkit-plots | Quick map or point time series PNG |
| `ecmwf_access.py` | all | stdlib | Which routes have credentials (no secrets printed) |
| `cds.py` | cds-ads | stdlib (+earthkit-data[cds] for retrieve) | Check, search, describe, validate + cost, ERA5 point, retrieve |
| `marsrequest.py` | mars | earthkit-data[mars] | Validate + submit |
| `ptpoint.py` | polytope | earthkit-data[polytope] | Access check, point series, ensemble percentiles |
| `wms.py` | opencharts-wms | stdlib (+Pillow via uv to composite) | Layers, times, GetMap image/URL, legend, GetFeatureInfo |
| `opencharts.py` | opencharts-wms | stdlib | Search, options, download official charts |
| `webmap.py` | opencharts-wms | stdlib | Create Leaflet web map; serve with click → meteogram (calls odcatalog/odpoint/ekplot) |

## 6. Generated references (weekly CI)

Open Data contents (live listings + `.index`), parameter DB, WMS GetCapabilities, PyPI versions of
eligible earthkit components. `scripts/regenerate_references.py [--check]`.

## 7. Testing

See `AGENTS.md` → *Testing*. Three layers: unit (pytest, offline, fixtures), live (pytest `-m live`,
hits ECMWF), skill evals (fresh headless agents with only the plugin loaded, graded by assertions).

## 8. Flagship demo

> Using the ECMWF skills, build me a web map with the latest ECMWF forecast layers and a time slider,
> and when I click a point show a 10-day meteogram for that location.

Research finding: the public WMS has **no 2 m temperature or precipitation layers** (public met
layers are MSLP, z500, t850, ws850 and ENS mean/spread). Plan: WMS public layers for the map +
OpenCharts images as an option; the clicked-point meteogram comes from Open Data via `odpoint.py`
(2 m temperature, precipitation, wind). Ask ECMWF about adding public surface layers (TODO).

## 9. Milestones

0. Scaffold ✅ · Research ✅
1. M1 — open-data + earthkit ✅
2. M2 — opencharts-wms + flagship demo ✅ (wms.py, opencharts.py, webmap.py; verified in a browser)
3. M3 — polytope ✅ (ptpoint.py: hourly point + 50-member ensemble percentiles, verified live)
4. M4 — cds-ads ✅ (cds.py: check/search/describe/validate+costing keyless; retrieve via earthkit-data[cds])
5. M5 — mars
6. M6 — tooling & CI → v1.0.0

## 10. Open questions → best guesses

See `docs/research/open-questions.md`. Summary: attribution wording adopted per §4.4; public WMS at
`eccharts.ecmwf.int/wms/?token=public` (CORS ok, 302 redirects, XML errors with HTTP 200); Polytope has
no keyless access; icon = neutral placeholder pending ECMWF comms approval of the org logo.
