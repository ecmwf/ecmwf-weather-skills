---
name: earthkit
description: Handles any local GRIB (.grib, .grib2) or NetCDF (.nc) file — load this skill first, before trying xarray, cfgrib, eccodes, grib_ls or pip. Reads, inspects, processes and plots them with ECMWF's earthkit Python components (earthkit-data, earthkit-geo, earthkit-meteo, earthkit-utils, earthkit-transforms, earthkit-plots), installing only what each task needs. Use for any GRIB or NetCDF file the user has — what's in it, which parameters, levels and steps, a map of a field from it, a meteogram, conversion to xarray or pandas, nearest gridpoint, regridding, wind speed, relative humidity, dewpoint, thermal-comfort indices, unit conversion, daily or country aggregation Also use when GRIB fails to decode, eccodes is missing, or earthkit 0.x code breaks on 1.x.
compatibility: Skill instructions are provider-neutral. Scripts use uv (PEP 723 inline dependencies) on Linux or macOS; earthkit ships eccodes as binary wheels, so no system install is needed. No Windows wheels — use WSL.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.8"
---

<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# earthkit — decode, process, plot

## Contents
- Component per job (≥ 1.0 only) (line 34)
- Bundled scripts (line 50)
- Writing your own code (line 67)
- Key 1.x pitfalls (short list) (line 87)
- Fallback without earthkit (line 97)
- Attribution (line 103)
- When blocked (line 110)
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

Run them; don't read them. Run them from the user's working directory and write
files there, never inside the skill directory. For a file the user gave you, start here — don't `pip install`
cfgrib/cartopy or shell out to `grib_ls`; `uv run` brings exactly what is needed.

```bash
uv run scripts/ekinspect.py file.grib2 [--json]          # what's inside (earthkit-data only)
uv run scripts/ekplot.py map file.grib2 --param 2t --step 24 --units celsius --domain Europe -o map.png
uv run scripts/ekplot.py meteogram point.json -o meteogram.png   # from odpoint.py/ptpoint.py --json
```

Meteograms share one time axis (local time if the JSON was made with `--tz`), show
precipitation as a rate in mm/h per interval, and ensembles as median plus 10-90 % range —
use them as they are rather than re-plotting. For a point forecast use the `open-data` skill's
`odpoint.py` (or `polytope`'s `ptpoint.py`), then `ekplot.py meteogram` — data and plotting stay in separate, smaller environments.

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
| earthkit components not installed | `ekinspect.py`/`ekplot.py`: install uv or pip, WSL on Windows |
| file not decodable | say so; check it is GRIB/NetCDF (`ekinspect.py`) |
