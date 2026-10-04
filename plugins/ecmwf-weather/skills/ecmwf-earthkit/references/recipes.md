<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)

SPDX-License-Identifier: Apache-2.0
-->

# earthkit recipes (1.x)

## Contents
- Read and inspect — earthkit-data
- Fetch — earthkit-data source extras
- Nearest gridpoint — earthkit-geo
- Regrid — earthkit-geo
- Derived quantities — earthkit-meteo / thermofeel
- Units — earthkit-utils
- Aggregation — earthkit-transforms[all]
- Plots — earthkit-plots

Each recipe lists the minimal PEP 723 dependencies. Run with `uv run script.py`.

## Read and inspect — earthkit-data

```python
# dependencies = ["earthkit-data>=1.2"]
import earthkit.data as ekd
fl = ekd.from_source("file", "forecast.grib2").to_fieldlist()
fl.ls()                                        # table of fields
t2 = fl.sel({"metadata.shortName": "2t"})      # select by short name
t850 = fl.sel({"metadata.shortName": "t", "metadata.level": 850})
f = t2[0]
f.time.base_datetime(), f.time.valid_datetime(), f.time.step()
lat, lon = f.geography.latlons()
vals = f.to_numpy()                            # 2-D array
ds = fl.to_xarray(time_dims=["valid_time"])    # xarray with a valid_time dimension
```

## Fetch — earthkit-data source extras

```python
# dependencies = ["earthkit-data[ecmwf-opendata]>=1.2"]
fl = ekd.from_source("ecmwf-open-data", model="ifs", param=["2t", "tp"], levtype="sfc",
                     step=[0, 24, 48]).to_fieldlist()           # no date -> latest run
# [cds]:      ekd.from_source("cds", "reanalysis-era5-single-levels", request_dict)
# [cds]:      ekd.from_source("ads", "cams-global-atmospheric-composition-forecasts", request_dict)
# [mars]:     ekd.from_source("mars", request_dict)
# [polytope]: ekd.from_source("polytope", "ecmwf-mars", request_dict)
```

Check credentials **before** calling cds/ads/mars sources — missing credentials trigger an
interactive prompt that hangs non-interactive agents.

## Nearest gridpoint — earthkit-geo

```python
# dependencies = ["earthkit-data>=1.2", "earthkit-geo>=1.1"]
from earthkit.geo import distance
lat, lon = fl[0].geography.latlons()
idx, dist_m = distance.nearest_point_kdtree((38.72, -9.14), (lat, lon))
i = int(idx[0])
series = [f.to_numpy(flatten=True)[i] for f in t2]
```

Don't use `to_pandas(latitude=…, longitude=…)` on gridded GRIB — it returns an empty frame.

## Regrid — earthkit-geo

```python
# dependencies = ["earthkit-data>=1.2", "earthkit-geo>=1.1"]
import earthkit.geo as eg
coarse = eg.regrid(fl, out_grid={"grid": [1, 1]}, interpolation="linear")
coarse.to_target("file", "coarse.grib2")
```

`interpolation`: `linear`, `nearest-neighbour`, `grid-box-average` (conservative-like, for
precipitation). MIR caches weights — set `MIR_CACHE_PATH` to a writable directory in sandboxes.

## Derived quantities — earthkit-meteo / thermofeel

```python
# dependencies = ["earthkit-meteo>=1.2"]
from earthkit.meteo import wind, thermo
speed = wind.speed(u, v)
direction = wind.direction(u, v, convention="meteo")   # direction wind blows FROM
rh = thermo.relative_humidity_from_dewpoint(t_K, td_K)
```

```python
# dependencies = ["thermofeel>=2.3"]
import thermofeel as tf
utci = tf.calculate_utci(t2_k=t_K, va=speed, mrt=mrt_K, td_k=td_K)
hi = tf.calculate_heat_index_adjusted(t2_k=t_K, td_k=td_K)
```

Inputs work on numpy arrays, xarray or FieldLists.

## Units — earthkit-utils

```python
# dependencies = ["earthkit-utils>=1.0"]
from earthkit.utils.units import convert_units
t_c = convert_units(t_K, "degC", source_units="K")
p_hpa = convert_units(msl_Pa, "hPa", source_units="Pa")
```

## Aggregation — earthkit-transforms[all]

```python
# dependencies = ["earthkit-data>=1.2", "earthkit-transforms[all]>=1.0", "earthkit-geo>=1.1"]
import earthkit.transforms as ekt
import earthkit.geo as eg
da = fl.sel({"metadata.shortName": "2t"}).to_xarray(time_dims=["valid_time"])["2t"]
daily_max = ekt.temporal.daily_max(da, time_dim="valid_time")
countries = eg.gisco.countries(resolution="60M").to_pandas()
pt_mean = ekt.spatial.reduce(da, countries[countries.CNTR_ID == "PT"], how="mean")
```

Always pass `time_dim="valid_time"` with forecast data (default dims are `step`).

## Plots — earthkit-plots

```python
# dependencies = ["earthkit-plots>=1.0"]
import earthkit.plots as ekp
fig = ekp.quickplot(t2[4], units="celsius", domain="Europe")
fig.save("t2m.png")

ts = ekp.Figure(rows=2, columns=1, figsize=(9, 5))
ts.add_timeseries().line(temperature_da)     # 1-D xarray with a time dim
ts.add_timeseries().bar(precip_da)
ts.save("meteogram.png")
```

Map helpers: `ekp.geo.{plot,contourf,contour,pcolormesh,quiver,barbs,streamplot,spaghetti}`.
Time series: `ekp.timeseries.{line,bar,scatter,fill_between,multiboxplot,stripes}` (use
`multiboxplot` for ensemble plumes). There is no dedicated meteogram helper.
