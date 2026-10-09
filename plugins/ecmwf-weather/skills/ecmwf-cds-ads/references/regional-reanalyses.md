<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# CARRA and CERRA on the CDS

Verified against the live CDS (forms, costing and real downloads).

## Contents
- Requests
- Subsetting
- Point time series (CERRA-Land)
- Limits
- Citation

## Requests

Common to the gridded datasets: `product_type` `analysis` (3-hourly `time` 00:00–21:00) or
`forecast` (`leadtime_hour` 1–6, 9, 12 … 30; long leads only from 00 and 12 UTC); `year`,
`month`, `day` as lists; `data_format` `grib` (much smaller than netcdf for these grids).

```json
{"domain": "west_domain", "level_type": "surface_or_atmosphere",
 "variable": ["2m_temperature"], "product_type": "analysis",
 "year": ["2023"], "month": ["07"], "day": ["18"], "time": ["12:00"],
 "data_format": "grib",
 "area": [67, -25, 63, -13], "grid": [0.05, 0.05]}
```

That is `reanalysis-carra-single-levels` over Iceland. CERRA single levels: no `domain`; add
`"data_type": ["reanalysis"]` (or `ensemble_members`). Run `cds.py describe ID` for the variable
names and levels of each dataset.

## Subsetting

| Request | Result |
|---|---|
| no `area`, no `grid` | whole Lambert domain: CARRA-West 1069×1269, CARRA-East 789×989, CERRA 1069×1069 points |
| `area` only | **job fails** ("croppedRepresentation() not implemented") — MARS cannot crop Lambert grids |
| `grid` only | regular lat/lon covering the domain's bounding box; points outside the domain are missing |
| `area` + `grid` | regular lat/lon, cropped to the area — use this for a region |

`cds.py validate` flags `area` without `grid`. Choose the grid near the native spacing: 0.025°
for CARRA, 0.05° for CERRA. Wind components on the native grid follow the Lambert axes, not
true north; regridded output is on a regular lat/lon grid. pan-CARRA's form has `area`, but it
masks rather than crops (the file keeps all 8.2 million points) — add `grid` there too.

To keep the native grid instead: download the domain and select by the 2-D latitude/longitude
(earthkit-data `to_xarray()`, then a mask) — see the `ecmwf-earthkit` skill.

## Point time series (CERRA-Land)

`reanalysis-cerra-land-timeseries`: five variables (`total_precipitation` daily, and others),
`csv` or `netcdf`, by `location` (snaps to the nearest 0.05° point) or a small `area` (0.1–0.5°):

```json
{"variable": ["total_precipitation"], "location": {"latitude": 38.72, "longitude": -9.14},
 "date": ["2020-01-01/2020-01-31"], "data_format": "csv"}
```

The CSV arrives zipped; `cds.py retrieve` unpacks it. Daily precipitation is the 24 h ending at
06 UTC. There is no CARRA or CERRA atmosphere time-series dataset: for a point, download a small
`area` + `grid` and take the nearest gridpoint.

## Limits

Cost is the number of fields (dates × times × steps × variables × levels); limits per request:
CARRA 120,000, CERRA 90,000, pan-CARRA 12,000, CERRA-Land time series 500 (a point costs 100 per
variable). `cds.py validate` asks the server. Small requests finish in about a minute; many
parallel requests are queued one after another.

## Citation

Licence: CC BY 4.0 (the CDS "CC-BY licence", one acceptance covers all). Attribution: "Contains
modified Copernicus Climate Change Service information <year>…" (regridded data is modified).
Cite the dataset DOI from `cds.py describe`: CARRA single levels 10.24381/cds.713858f6, CERRA
single levels 10.24381/cds.622a565a, CERRA-Land 10.24381/cds.a7f3cd0b, pan-CARRA
10.24381/f5effe24.
