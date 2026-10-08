<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)

SPDX-License-Identifier: Apache-2.0
-->

# CDS / ADS datasets and requests

Confirm ids and values with `cds.py search` / `cds.py describe` — the catalogue changes.

## Contents
- Common datasets
- Request examples
- Climate normals recipe
- Limits and queue
- Errors

## Common datasets

| Need | Store | Dataset id |
|---|---|---|
| ERA5 hourly at a point (fast) | cds | `reanalysis-era5-single-levels-timeseries` |
| ERA5 hourly, gridded, surface | cds | `reanalysis-era5-single-levels` |
| ERA5 hourly, pressure levels | cds | `reanalysis-era5-pressure-levels` |
| ERA5 monthly means | cds | `reanalysis-era5-single-levels-monthly-means` |
| ERA5 daily statistics (pre-computed) | cds | `derived-era5-single-levels-daily-statistics` |
| ERA5-Land hourly, 0.1° | cds | `reanalysis-era5-land` (point: `reanalysis-era5-land-timeseries`) |
| Seasonal forecasts, monthly | cds | `seasonal-monthly-single-levels` |
| Thermal comfort (UTCI) history | cds | `derived-utci-historical` |
| CAMS global forecasts | ads | `cams-global-atmospheric-composition-forecasts` |
| CAMS Europe air quality forecasts | ads | `cams-europe-air-quality-forecasts` |
| CAMS global reanalysis | ads | `cams-global-reanalysis-eac4` |

## Request examples

ERA5 gridded, one month of daily-noon precipitation over Portugal:

```json
{"product_type": ["reanalysis"], "variable": ["total_precipitation"], "year": ["2020"],
 "month": ["01"], "day": ["01", "02", "03"], "time": ["12:00"],
 "area": [42.2, -9.6, 36.9, -6.1], "data_format": "netcdf", "download_format": "unarchived"}
```

`area` is `[north, west, south, east]`. List every day (`"01"`…`"31"`); invalid dates for a
month are ignored by the server.

ERA5 point time series:

```json
{"variable": ["2m_temperature"], "location": {"latitude": 38.72, "longitude": -9.14},
 "date": ["1991-01-01/2020-12-31"], "data_format": "csv"}
```

CAMS Europe forecast (ADS):

```json
{"variable": ["particulate_matter_2.5um"], "model": ["ensemble"], "level": ["0"],
 "date": ["2026-10-02/2026-10-02"], "type": ["forecast"], "time": ["00:00"],
 "leadtime_hour": ["0", "12", "24"], "data_format": "netcdf_zip"}
```

## Climate normals recipe

ERA5 point series (`cds.py era5-point`, CSV with columns `valid_time, t2m, latitude,
longitude`) straight into earthkit-transforms and earthkit-utils:

```python
# /// script
# dependencies = ["earthkit-transforms[all]>=1.0", "earthkit-utils>=1.0", "pandas>=2"]
# ///
import pandas as pd
from earthkit.transforms import climatology, temporal
from earthkit.utils.units import convert_units

da = pd.read_csv("lisbon.csv", parse_dates=["valid_time"]).set_index("valid_time")["t2m"]
da = convert_units(da.to_xarray().assign_attrs(units="K"), "degC")
daily_max = temporal.daily_max(da, time_dim="valid_time")
normals = climatology.mean(da, frequency="month", time_dim="valid_time")   # 1991-2020 → normals
print(daily_max.values, normals.values)
```

Anomalies: `climatology.anomaly(da, normals, frequency="month")`. Gridded NetCDF: read with
earthkit-data and use the same functions — see the `ecmwf-earthkit` skill.

## Limits and queue

- `validate` reports the server's `cost` and `limit` for the request; over the limit → split.
- Requests are queued; retrieval of gridded ERA5 from tape can take a long time. Point time
  series and derived datasets are served from fast storage.

## Errors

| Message | Fix |
|---|---|
| licence / terms not accepted | accept on the dataset page (Download tab, "Terms of use") |
| 401 / authentication | key missing or wrong in `~/.cdsapirc`; URL must be `https://cds.climate.copernicus.eu/api` |
| request too large / cost exceeds limit | split by year/month, reduce area or variables |
| no data for combination | some variables/levels/times don't exist together — `describe` and the form show valid values |
