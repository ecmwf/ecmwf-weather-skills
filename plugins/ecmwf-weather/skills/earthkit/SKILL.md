---
name: earthkit
description: Reads, processes and plots ECMWF GRIB and NetCDF data with the earthkit Python components (earthkit-data, earthkit-geo, earthkit-meteo, earthkit-utils, earthkit-transforms, earthkit-plots) — installing only the component each task needs. Use when a task has a GRIB or NetCDF file to inspect, convert to xarray or pandas, select fields by short name, level or step, extract a nearest gridpoint, regrid, compute wind speed, relative humidity, dewpoint or thermal-comfort indices, convert units, aggregate daily or over a country or polygon, draw a weather map, or draw a meteogram from a point forecast; or when GRIB fails to decode, eccodes is missing, or earthkit code written for 0.x breaks on 1.x.
compatibility: Skill instructions are provider-neutral. Scripts use uv (PEP 723 inline dependencies) on Linux or macOS; earthkit ships eccodes as binary wheels, so no system install is needed. No Windows wheels — use WSL.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.1"
---

<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# earthkit — decode, process, plot

## Contents
- Component per job (install only what the task needs)
- Bundled scripts
- Writing your own code
- Key 1.x pitfalls
- Fallback without earthkit
- Attribution (required)
- References — `references/recipes.md` (code per job), `references/pitfalls.md` (0.x → 1.x breakages), `references/versions.md` (generated: latest versions, ≥ 1.0 eligibility)

earthkit is ECMWF's Python toolkit. It is split into components; **install only what the task
needs** and never the `earthkit` meta-package or `earthkit-data[all]` (≈230 MB, 100+ packages).

Use ECMWF sources only — never substitute a third-party weather API; if no ECMWF route works,
say so.

## Component per job (≥ 1.0 only)

| Job | Component | Install spec | ≈ Size |
|---|---|---|---|
| Read GRIB/NetCDF, select, to xarray/pandas, fetch | `earthkit-data` | `earthkit-data>=1.2` (+ `[ecmwf-opendata]`, `[cds]`, `[mars]`, `[polytope]` for that source) | 64 MB |
| Nearest gridpoint, regrid, country polygons | `earthkit-geo` | `earthkit-geo>=1.1` | 90 MB |
| Wind, humidity, thermodynamics, EFI/SOT, scores | `earthkit-meteo` | `earthkit-meteo>=1.2` | 18 MB |
| Unit conversion | `earthkit-utils` | `earthkit-utils>=1.0` | 1 MB |
| Daily/monthly stats, deaccumulate, polygon/country means, anomalies | `earthkit-transforms` | `earthkit-transforms[all]>=1.0` (plain install fails on import) | 150 MB |
| Maps, time series, meteograms | `earthkit-plots` | `earthkit-plots>=1.0` | 159 MB |
| UTCI, heat index, wind chill, humidex, WBGT | `thermofeel` (ECMWF, not earthkit) | `thermofeel>=2.3` | small |

Do not use: `earthkit-regrid` (deprecated → earthkit-geo), `earthkit-maps` (→ plots),
`earthkit-aggregate` (→ transforms), `earthkit-time`, `earthkit-climate`, `earthkit-workflows`
(< 1.0; the last two need earthkit-data < 1 and conflict).

## Bundled scripts

Run them; don't read them.

```bash
uv run scripts/ekinspect.py file.grib2 [--json]          # what's inside (earthkit-data only)
uv run scripts/ekplot.py map file.grib2 --param 2t --step 24 --units celsius --domain Europe -o map.png
uv run scripts/ekplot.py meteogram point.json -o meteogram.png   # from open-data odpoint.py --json
```

For a point forecast from Open Data use the `open-data` skill's `odpoint.py`, then
`ekplot.py meteogram` — data and plotting stay in separate, smaller environments.

## Writing your own code

Declare only the needed components in PEP 723 metadata and run with `uv run`:

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-meteo>=1.2"]
# ///
import earthkit.data as ekd
from earthkit.meteo import wind

fl = ekd.from_source("file", "forecast.grib2").to_fieldlist()
u = fl.sel({"metadata.shortName": "10u"}).to_numpy()
v = fl.sel({"metadata.shortName": "10v"}).to_numpy()
speed = wind.speed(u, v)
```

Recipes for every job above: `references/recipes.md`. 0.x → 1.x pitfalls:
`references/pitfalls.md` — read it before writing earthkit code; most online examples are 0.x.

## Key 1.x pitfalls (short list)

- Select with `fl.sel({"metadata.shortName": "2t"})` — `fl.sel(param="2t")` **silently returns
  nothing**.
- `f.metadata("key")` has no `default=`; use `f.get("metadata.key", default=None)`.
- `from_source(...)` returns a source object — call `.to_fieldlist()` or `.to_xarray()`.
- There is no `ekp.plot`; use `ekp.quickplot` or `ekp.geo.*` / `ekp.timeseries.*`.
- Library banners print to stdout — redirect if your script emits JSON.
- Pin `>=1`: on platforms without wheels pip silently installs 0.x with a different API.

## Fallback without earthkit

Only if uv/pip cannot install (no network to PyPI, Windows without WSL) or the user declines:
GRIB cannot be decoded with the standard library. Say so, and offer the install command for just
the components needed. NetCDF from CDS can sometimes be read with an existing `xarray`/`netCDF4`.

## Attribution

Required wherever results are shown. Figures and derived data from ECMWF sources keep the source licence. For Open Data:
`Data: © <year> ECMWF, CC BY 4.0` (the scripts stamp it on every figure). For Copernicus
(ERA5/CAMS): "Generated using Copernicus Climate Change Service / Atmosphere Monitoring Service
information <year>". State modifications (regridded, interpolated, unit-converted).
