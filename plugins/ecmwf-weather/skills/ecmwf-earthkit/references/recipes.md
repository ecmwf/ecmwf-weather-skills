<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# earthkit recipes (1.x, every block is executed by the test suite)

Each block lists its minimal PEP 723 dependencies; run with `uv run script.py`. Files used:
`forecast.grib2` (2t, tp, 10u, 10v, msl, tcc at several steps), `ens.grib2` (2t ensemble
members). Blocks marked `# needs network` download data.

## Contents
- Read, select, inspect — earthkit-data
- Fetch — earthkit-data sources
- Ensemble mean, spread and percentiles — earthkit-transforms + earthkit-plots
- Maps — earthkit-plots
- Multi-panel figures — earthkit-plots
- Time series, meteograms, plumes — earthkit-plots
- Daily statistics, de-accumulation, rates — earthkit-transforms
- Climatology and anomalies — earthkit-transforms
- Areas and countries — earthkit-geo + earthkit-transforms
- Meteorological quantities — earthkit-meteo
- Extremes and scores — earthkit-meteo
- Regrid, nearest point, distance — earthkit-geo
- Units — earthkit-utils
- Write GRIB — earthkit-data
- Rivers — earthkit-hydro
- Forecast and hindcast dates — earthkit-time (Emerging)

## Read, select, inspect — earthkit-data

```python
# /// script
# dependencies = ["earthkit-data>=1.2"]
# ///
import earthkit.data as ekd

fl = ekd.from_source("file", "forecast.grib2").to_fieldlist()   # always .to_fieldlist()
fl.ls()                                                           # table of fields
t2 = fl.sel({"parameter.variable": "2t"})                         # dict keys, never sel(param=)
pair = fl.sel({"parameter.variable": ["2t", "msl"], "time.step": [6, 12]})
print(len(fl), len(t2), len(pair), fl.unique("parameter.variable"))
f = t2[0]
print(f.time.base_datetime(), f.time.valid_datetime(), f.time.step(), f.get("parameter.units"))
lat, lon = f.geography.latlons()
ds = fl.to_xarray(time_dims=["valid_time"])        # valid_time axis for temporal statistics
print(dict(ds.sizes))
```

Concatenate FieldLists with `ekd.concat(a, b)` (`a + b` is arithmetic). Slice with `fl[2:5]`.

## Fetch — earthkit-data sources

```python
# /// script
# dependencies = ["earthkit-data[ecmwf-opendata]>=1.2"]
# ///
# needs network
import earthkit.data as ekd

ens = ekd.from_source("ecmwf-open-data", model="ifs", stream="enfo", type="pf", param="2t",
                      step=24, number=[1, 2, 3, 4, 5]).to_fieldlist()     # no date = latest
print(len(ens))
# [cds]:      ekd.from_source("cds", "reanalysis-era5-single-levels", request)
# [cds]:      ekd.from_source("ads", "cams-global-atmospheric-composition-forecasts", request)
# [mars]:     ekd.from_source("mars", request)
# [polytope]: ekd.from_source("polytope", "ecmwf-mars", request, address="polytope.ecmwf.int")
```

Check credentials first (the skills' `check` commands): missing credentials make cds/ads/mars
prompt interactively.

## Ensemble mean, spread and percentiles — earthkit-transforms + earthkit-plots

Ready-made: `uv run scripts/ekplot.py ens-stats ens.grib2 --param 2t --units celsius
--domain Europe -o ens.png`. The same in code:

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-transforms[all]>=1.0", "earthkit-plots>=1.0"]
# ///
import earthkit.data as ekd
import earthkit.plots as ekp
import earthkit.transforms as ekt
from earthkit.transforms import ensemble

da = ekd.from_source("file", "ens.grib2").to_fieldlist().to_xarray()["2t"]  # dim "member"
mean, spread = ensemble.mean(da), ensemble.std(da)
p90 = ekt.reduce(da, how="percentile", dim="member", q=90)

fig = ekp.Figure(rows=1, columns=2, size=(12, 4.5))
m = fig.add_map(0, 0, domain="Europe")
m.contourf(mean, units="celsius", style="auto")        # ECMWF temperature style, °C
m.legend(label="2 m temperature — ensemble mean")
s = fig.add_map(0, 1, domain="Europe")
s.contourf(spread, colors="Purples", extend="max")      # a spread is not a temperature
s.legend(label="2 m temperature — ensemble standard deviation (K)")
fig.coastlines()
fig.attribution("Data: © ECMWF, CC BY 4.0")
fig.save("ens_mean_std.png")
print(float(p90.max()))
```

Never `units="celsius"` or `style="auto"` on a spread: it keeps the variable's metadata, so the
temperature offset and palette would be applied to a difference.

## Maps — earthkit-plots

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-plots>=1.0"]
# ///
import earthkit.data as ekd
import earthkit.plots as ekp

fl = ekd.from_source("file", "forecast.grib2").to_fieldlist()
t2 = fl.sel({"parameter.variable": "2t", "time.step": 12})
ekp.geo.plot(t2, domain="Europe", units="celsius").save("t2m.png")   # style chosen from GRIB

m = ekp.Map(domain="North Atlantic")
m.contourf(t2, units="celsius", style="auto")      # style="auto" is NOT the default here
m.contour(fl.sel({"parameter.variable": "msl", "time.step": 12}), units="hPa", style="auto")
m.quiver(fl.sel({"parameter.variable": "10u", "time.step": 12}),
         fl.sel({"parameter.variable": "10v", "time.step": 12}))
m.coastlines()
m.gridlines()
m.legend()
m.title("{variable_name} — {valid_time:%a %d %b %Y %H UTC} (T+{lead_time})")
m.save("synoptic.png")
print(len(ekp.list_styles()))
```

Domains: named regions (`Europe`, `North Atlantic`, `Mediterranean`), any country name, or
`[W, E, S, N]`; projections via `crs=` (cartopy CRS). Named ECMWF styles:
`ekp.load_style("2m-temperature-spectral-celsius")`; custom:
`ekp.styles.Style(levels=..., colors=..., units=..., extend="both")`. Pass the result of
`.to_fieldlist()`/`.sel()` — a raw `from_source` object plots only its first field.

## Multi-panel figures — earthkit-plots

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-plots>=1.0"]
# ///
import earthkit.data as ekd
import earthkit.plots as ekp

t2 = ekd.from_source("file", "forecast.grib2").to_fieldlist().sel({"parameter.variable": "2t"})
ekp.geo.plot(t2, groupby="time.step", columns=3, title="T+{time.step}", units="celsius",
             figsize=(12, 4)).save("t2m_steps.png")
```

## Time series, meteograms, plumes — earthkit-plots

Point series: `odpoint.py` / `ptpoint.py --json`, then `ekplot.py meteogram`. For ensemble
plumes from xarray (`member` × `valid_time`):

```python
# /// script
# dependencies = ["earthkit-plots>=1.0", "numpy", "xarray"]
# ///
import earthkit.plots as ekp
import numpy as np
import xarray as xr

t = np.arange("2026-10-03T00", "2026-10-05T00", 6, dtype="datetime64[h]").astype("datetime64[ns]")
pt = xr.DataArray(285 + np.random.default_rng(1).normal(0, 1, (10, t.size)),
                  dims=["member", "valid_time"], coords={"valid_time": t}, name="2t",
                  attrs={"units": "K", "long_name": "2 metre temperature"})
fig = ekp.Figure(rows=1, columns=1, size=(9, 3))
ts = fig.add_timeseries()
for m in range(pt.sizes["member"]):               # one line per member (2-D input mislabels)
    ts.line(pt.isel(member=m), units="celsius", color="grey", alpha=0.4)
ts.envelope(pt.min("member"), pt.max("member"), units="celsius")
fig.save("plume.png")
ekp.timeseries.multiboxplot(pt, units="celsius").save("boxes.png")
```

Stacked panels: `ekp.Figure(rows=2, columns=1)` with `fig.add_timeseries(0, 0).line(...)` and
`fig.add_timeseries(1, 0).bar(..., width=..., align="edge")`.

## Daily statistics, de-accumulation, rates — earthkit-transforms

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-transforms[all]>=1.0"]
# ///
import earthkit.data as ekd
from earthkit.transforms import temporal

fl = ekd.from_source("file", "forecast.grib2").to_fieldlist()
ds = fl.to_xarray(time_dims=["valid_time"])        # temporal needs a datetime dimension
print(temporal.daily_max(ds["2t"], time_dim="valid_time").sizes)
tp = ds["tp"]
per_step = temporal.deaccumulate(tp)                # amount in each interval (one fewer step)
rate = temporal.accumulation_to_rate(tp, accumulation_type="start_of_forecast",
                                     rate_units="hours")
print(per_step.sizes, rate.attrs.get("units"))
```

Also `daily_mean/min/sum/std/median`, `monthly_*`, `rolling_reduce(da, window_length=,
how_reduce=)`, `daily_reduce(da, how=, time_shift={"hours": -1})`. IFS `tp` is accumulated
from the start of the forecast — say so with `accumulation_type="start_of_forecast"`.

## Climatology and anomalies — earthkit-transforms

```python
# /// script
# dependencies = ["earthkit-transforms[all]>=1.0", "numpy", "pandas", "xarray"]
# ///
import numpy as np
import pandas as pd
import xarray as xr
from earthkit.transforms import climatology

t = pd.date_range("1991-01-01", "2020-12-31", freq="MS")
da = xr.DataArray(10 + 8 * np.sin(2 * np.pi * (t.month - 4) / 12), dims=["time"],
                  coords={"time": t}, name="t2m")
clim = climatology.mean(da, frequency="month")                   # dim "month"
anom = climatology.anomaly(da, clim, frequency="month")
q = climatology.quantiles(da, [0.1, 0.9], frequency="month")
print(clim.sizes, float(abs(anom).max()), q.sizes)
```

`climatology.auto_anomaly(da, climatology_range=("1991", "2020"))` — the range as strings.
ERA5 point series from the `ecmwf-cds-ads` skill (`cds.py era5-point`) go straight in.

## Areas and countries — earthkit-geo + earthkit-transforms

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-geo>=1.1", "earthkit-transforms[all]>=1.0"]
# ///
# needs network (country shapes from GISCO)
import earthkit.data as ekd
from earthkit.geo import gisco
from earthkit.transforms import spatial

da = ekd.from_source("file", "forecast.grib2").to_fieldlist().to_xarray(
    time_dims=["valid_time"])["2t"]
da = da.assign_coords(longitude=((da.longitude + 180) % 360) - 180).sortby("longitude")
box = spatial.reduce(da, area={"north": 60, "south": 35, "west": -15, "east": 30},
                     weights="latitude")
countries = gisco.countries(resolution="60M").to_geopandas()
pt = spatial.reduce(da, countries[countries.CNTR_ID == "PT"], mask_dim="CNTR_ID",
                    weights="latitude")
print(box.sizes, pt.sizes)
```

Shift longitudes to −180…180 first: on 0–360 grids western-hemisphere polygons come back NaN.
NUTS regions: `gisco.nuts_regions(level=1, resolution="60M")`.

## Meteorological quantities — earthkit-meteo

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-meteo>=1.2", "numpy"]
# ///
from datetime import datetime

import earthkit.data as ekd
import numpy as np
from earthkit.meteo import solar, thermo, wind

fl = ekd.from_source("file", "forecast.grib2").to_fieldlist()
u = fl.sel({"parameter.variable": "10u"})
v = fl.sel({"parameter.variable": "10v"})
speed = wind.speed(u, v)                          # FieldList in, FieldList out (10si)
direction = wind.direction(u, v, convention="meteo")
print(speed[0].get("parameter.variable"), float(speed.to_numpy().max()))
t, td = np.array([[293.15]]), np.array([[283.15]])
print(thermo.relative_humidity_from_dewpoint(t, td))            # % (52.5)
print(thermo.wet_bulb_temperature_from_dewpoint(t, td, np.array([[101325.0]]),
                                                t_method="newton"))
print(thermo.potential_temperature(t, np.array([[85000.0]])))
print(solar.cos_solar_zenith_angle(datetime(2026, 6, 21, 12), np.array(38.7), np.array(-9.1)))
```

Wrap scalars in `np.array` (plain floats are rejected); dates are `datetime`, not `np.datetime64`. Scores need the `earthkit-meteo[scores]` extra (the library's own error message says `[score]`, which doesn't exist). xarray results keep the input's name
and attributes — rename them before plotting or writing. Thermal-comfort indices (UTCI, heat
index, wind chill) are in `thermofeel`, not earthkit-meteo.

## Extremes and scores — earthkit-meteo

```python
# /// script
# dependencies = ["earthkit-meteo[scores]>=1.2", "numpy", "xarray"]
# ///
import numpy as np
import xarray as xr
from earthkit.meteo import extreme, score

rng = np.random.default_rng(0)
clim = xr.DataArray(np.sort(rng.normal(0, 1, (101, 4)), axis=0), dims=["quantile", "point"])
ens = xr.DataArray(rng.normal(1, 1, (50, 4)), dims=["member", "point"])
obs = xr.DataArray(rng.normal(0, 1, 4), dims=["point"])
print(extreme.efi(clim, ens, clim_dim="quantile", ens_dim="member").values)
print(extreme.sot(clim, ens, 90, clim_dim="quantile", ens_dim="member").values)
print(score.crps_from_ensemble(ens, obs, over="member").values)
```

## Regrid, nearest point, distance — earthkit-geo

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-geo>=1.1"]
# ///
import earthkit.data as ekd
from earthkit.geo import regrid
from earthkit.geo.distance import GeoKDTree, haversine_distance

fl = ekd.from_source("file", "forecast.grib2").to_fieldlist()
coarse = regrid(fl, out_grid={"grid": [10, 10]}, interpolation="linear")
print(coarse[0].geography.grid_spec())
lat, lon = fl[0].geography.latlons()
idx, dist = GeoKDTree(lat.ravel(), lon.ravel()).nearest_point((38.72, -9.14))
series = fl.sel({"parameter.variable": "2t"}).to_numpy(flatten=True)[:, idx]
print(series, haversine_distance((38.72, -9.14), (51.5, -0.12)))
```

`interpolation`: `linear`, `nearest-neighbour`, `grid-box-average` (precipitation). Set
`MIR_CACHE_PATH` to a writable directory. Regridded longitudes run 0…360.

## Units — earthkit-utils

```python
# /// script
# dependencies = ["earthkit-utils>=1.0", "numpy"]
# ///
import numpy as np
from earthkit.utils.units import convert_array

print(convert_array(np.array([273.15, 300.0]), "degC", "K"))   # target units first
```

xarray: `convert_units(da, "hPa")` or `convert_units(ds, {"2t": "degC", "msl": "hPa"})`.
Not on FieldLists (silently does nothing) — convert at plot time with `units=` instead.

## Write GRIB — earthkit-data

```python
# /// script
# dependencies = ["earthkit-data>=1.2"]
# ///
import earthkit.data as ekd

fl = ekd.from_source("file", "forecast.grib2").to_fieldlist()
fl.sel({"parameter.variable": "msl"}).to_target("file", "msl.grib2")
print(len(ekd.from_source("file", "msl.grib2").to_fieldlist()))
```

## Rivers — earthkit-hydro

```python
# /// script
# dependencies = ["earthkit-hydro>=1.4", "numpy"]
# ///
# needs network (river network download, ~2 MB)
import earthkit.hydro as ekh
import numpy as np

net = ekh.river_network.load("cama_15min", "4")
upstream_cells = ekh.upstream.sum(net, np.ones(net.shape))
print(int(upstream_cells.max()), ekh.streamorder.strahler(net).max().item())
```

Catchments: `ekh.catchments.find(net, locations=[(row, col)])`; also `downstream.*`,
`distance.*`, `subnetwork.crop`. Networks: `ekh.river_network.available()`.

## Forecast and hindcast dates — earthkit-time (Emerging)

The one earthkit component below 1.0 allowed here, for date logic nothing else covers; pin it.

```python
# /// script
# dependencies = ["earthkit-time==0.1.8"]
# ///
from datetime import date

from earthkit.time import RelativeYear, WeeklySequence, model_climate_dates

runs = WeeklySequence([0, 3])                        # Monday and Thursday runs
print(runs.next(date(2026, 10, 4)))
dates = model_climate_dates(date(2026, 10, 5), RelativeYear(-20), RelativeYear(-1), 7, 7, runs)
print(len(list(dates)))
```
