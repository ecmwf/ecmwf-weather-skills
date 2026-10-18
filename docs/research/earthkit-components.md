<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)

SPDX-License-Identifier: Apache-2.0
-->

# Research — earthkit components (2026-10-18)

Method: PyPI release feeds for every `earthkit-*` package (and the GitHub organisation's
repository list), `uv pip compile` to see what each platform resolves, and the runnable recipes in
`plugins/ecmwf-weather/skills/ecmwf-earthkit/references/recipes.md`, which
`tests/earthkit/test_recipes.py` executes against the packages the recipes declare. ✅ = the call
is in a recipe that runs in that test, ⚠️ = needs credentials, so it is a template only.

Versions are not repeated here: `plugins/ecmwf-weather/skills/ecmwf-earthkit/references/versions.md`
is generated from PyPI (`make references`) and is the source of truth for every version, for the
≥ 1.0 rule and for the allowed exception. Download sizes are not tracked — they change with every
release and with the platform.

Re-run this research before each minor release (see AGENTS.md "Research step").

## Eligible (≥ 1.0) — use these

| Package | Purpose |
|---|---|
| `earthkit-data` | Read/fetch/convert GRIB, NetCDF, BUFR…; sources: file, url, ecmwf-open-data, cds, ads, mars, polytope |
| `earthkit-geo` | Regridding (MIR), nearest point, distances, rotation, GISCO country/NUTS polygons |
| `earthkit-meteo` | Wind, thermo (RH, dewpoint, θ, wet bulb), vertical, solar, EFI/SOT, scores, stats |
| `earthkit-plots` | Maps (`geo.plot`, `Map`, `Figure`) and time series (`timeseries.*`) |
| `earthkit-transforms[all]` | Temporal/spatial/climatology/ensemble aggregation — **import fails without `[all]`** |
| `earthkit-utils` | Unit conversion (`units.convert_units`, `convert_array`), array backends |
| `earthkit-hydro` | River networks, upstream/downstream, catchments |
| `thermofeel` (not earthkit) | UTCI, heat index, wind chill, humidex, apparent temperature, WBGT, MRT |

No new package reached 1.0 between the previous version of this note (2026-10-02) and 2026-10-18;
`earthkit-data`, `earthkit-geo`, `earthkit-meteo`, `earthkit-utils` and `earthkit-workflows` have
newer releases, listed in `versions.md`.

## Allowed exception below 1.0

`earthkit-time` (run and hindcast date sequences) is the one earthkit component below 1.0 the
skills use: nothing else covers date logic for forecast and hindcast runs. It is pinned exactly
(`==`, the version in `versions.md`) and marked "Emerging" there. `scripts/regenerate_references.py`
(`ALLOWED_EXCEPTIONS`), AGENTS.md ("earthkit first") and the `ecmwf-earthkit` skill agree on this.

## Not used (< 1.0, deprecated or too broad)

- `earthkit-regrid` — deprecated, use `earthkit-geo`.
- `earthkit-climate` and `earthkit-workflows` — both pull `earthkit-data` below 1 (checked with
  `uv pip compile` on 2026-10-18: together they resolve `earthkit-data` 0.20), which conflicts with
  every skill script.
- `earthkit-maps` — deprecated, use `earthkit-plots`; `earthkit-aggregate` — deprecated, use
  `earthkit-transforms`; `earthkit-plots-default-styles` is a dependency of `earthkit-plots`, never
  installed on its own.
- The `earthkit` meta-package is ≥ 1.0 but installs every component — **do not use**; likewise
  never `earthkit-data[all]`. Each script declares only the bundle it needs.
- Other repositories in the GitHub organisation (workflow add-ons, test images, demo sources, the
  hydro data store) are not packages the skills need.

## Function → component

The job → component map agents read is the "Before you write code" table in
`ecmwf-earthkit/SKILL.md`; the code is in `ecmwf-earthkit/references/recipes.md`. Summary:

| Function | Component | API |
|---|---|---|
| Read GRIB/NetCDF | earthkit-data | `ekd.from_source("file", p).to_fieldlist()` / `.to_xarray()` ✅ |
| Open Data fetch | earthkit-data`[ecmwf-opendata]` | `from_source("ecmwf-open-data", model=…, stream=…, type=…, param=…, step=…)`; omit `date` → latest ✅ |
| CDS / ADS | earthkit-data`[cds]` | `from_source("cds"\|"ads", dataset, request)` ⚠️ |
| MARS | earthkit-data`[mars]` | `from_source("mars", request)` ⚠️ |
| Polytope | earthkit-data`[polytope]` | `from_source("polytope", "ecmwf-mars", request, address=…)` ⚠️ |
| Select | earthkit-data | `fl.sel({"parameter.variable": "2t"})` ✅ — **not** `sel(param=…)` or `sel(shortName=…)` (silently empty on 1.x) |
| Time keys | earthkit-data | `f.time.base_datetime()/valid_datetime()/step()` ✅ |
| xarray | earthkit-data | `to_xarray(time_dims=["valid_time"])` ✅ |
| Write GRIB | earthkit-data | `fl.sel(…).to_target("file", path)` ✅ |
| Nearest gridpoint | earthkit-geo | `GeoKDTree(lat, lon).nearest_point((lat, lon))` from `earthkit.geo.distance` ✅ |
| Regrid | earthkit-geo | `earthkit.geo.regrid(fl, out_grid={"grid": [1, 1]}, interpolation="linear")` ✅ (MIR cache → set a writable `MIR_CACHE_PATH`) |
| Country / NUTS shapes | earthkit-geo | `gisco.countries()`, `gisco.nuts_regions()` ✅ |
| Wind speed/dir | earthkit-meteo | `wind.speed(u, v)` ✅, `wind.direction(u, v, convention="meteo")` ✅ |
| RH / dewpoint / θ / wet bulb | earthkit-meteo | `thermo.relative_humidity_from_dewpoint`, `potential_temperature`, `wet_bulb_temperature_from_dewpoint` ✅ |
| EFI / SOT / CRPS | earthkit-meteo`[scores]` | `extreme.efi`, `extreme.sot`, `score.crps_from_ensemble` ✅ |
| Thermal indices | thermofeel | `calculate_utci`, `calculate_heat_index_adjusted`, `calculate_wbgt`, … ✅ |
| Units | earthkit-utils | `units.convert_array(x, "degC", "K")`, `convert_units(da, "hPa")` on xarray ✅ — a FieldList is left unchanged; use `units=` at plot time |
| Temporal aggregation | earthkit-transforms[all] | `temporal.daily_max(da, time_dim="valid_time")`, `deaccumulate`, `accumulation_to_rate` ✅ |
| Climatology / anomalies | earthkit-transforms[all] | `climatology.mean / anomaly / quantiles` ✅ |
| Ensemble statistics | earthkit-transforms[all] | `ensemble.mean / std`, `ekt.reduce(…, how="percentile")` ✅ |
| Polygon/country | earthkit-transforms[all] + earthkit-geo | `spatial.reduce(da, gdf, mask_dim=…)` ✅ |
| Maps | earthkit-plots | `ekp.geo.plot(data, domain="Europe", units=…)`, `ekp.Map` ✅ — **`ekp.plot` does not exist** |
| Meteogram | earthkit-plots | no helper — compose `ekp.timeseries.*` (the skill's `ekplot.py meteogram`) ✅ |
| River networks | earthkit-hydro | `river_network.load`, `upstream.sum`, `catchments.find` ✅ |
| Run / hindcast dates | earthkit-time (pinned exception) | `WeeklySequence`, `model_climate_dates` ✅ |

Work outside earthkit, by design: the Open Data directory and index logic (`odcatalog.py`), the
ERA5 point series and licence checks (`cds.py`), the observation reports (`obs.py`) and the
ECMWF MCP client (`ecmwf_mcp.py`) are the skills' own scripts. The MCP server's tools are not
earthkit components.

## Install bundles (task-scoped, never all at once)

The PEP 723 block of each script is the source of truth for its bundle (`git grep -n
"dependencies" plugins`); the `earthkit` dependency group in `pyproject.toml` mirrors them. Every
script run with `uv` requires Python ≥ 3.12 and pins earthkit packages with `>=` at the floor it
needs:

| Bundle | Spec |
|---|---|
| fetch-opendata | `earthkit-data[ecmwf-opendata]>=1.2` |
| point | fetch-opendata + `earthkit-geo>=1.1` + `earthkit-utils>=1` |
| derive | `earthkit-meteo>=1.2` (+ `thermofeel>=2.3`) |
| plot | `earthkit-plots>=1.0` (pulls earthkit-data, cartopy) |
| aggregate | `earthkit-transforms[all]>=1.0` |
| cds / mars / polytope | `earthkit-data[cds]` / `[mars]` / `[polytope,covjsonkit]`, all `>=1.2` |
| date logic | `earthkit-time==` the pinned release |

## Platform caveats

Dependency resolution checked on 2026-10-18 with `uv pip compile` (resolver only, nothing
installed, Python 3.12):

- **Linux** (x86_64, aarch64): `earthkit-data>=1.2` resolves the latest release.
- **macOS** (arm64, x86_64): resolves one minor release behind the latest — the newest
  `eccodeslib` has no macOS wheel yet, so `earthkit-data>=1.3` fails to resolve there, while
  `>=1.2` still works.
- **Windows**: no solution for `earthkit-data>=1.2` — `eccodeslib` has no `win_amd64` wheel — and
  an unpinned `earthkit-data` silently resolves 0.20 (pre-1.0 API). Always pin `>=1` so it fails
  loudly; the stdlib fallback scripts cover Windows and say what they cannot do.
- Missing CDS/ADS/MARS credentials trigger an interactive `getpass` prompt — always pre-check
  credentials (CDS/MARS accept `prompt=False`; ADS does not). Read from earthkit source on
  2026-10-02; not re-checked on 2026-10-18.
- ADS credentials: only `~/.adsapirc` (no env vars in the earthkit path). Same status.
