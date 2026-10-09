<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# earthkit recipes (1.x, every block is executed by the test suite)

Each block lists its minimal PEP 723 dependencies; run with `uv run script.py`. Files used:
`forecast.grib2` (2t, tp, 10u, 10v, msl, tcc at several steps), `ens.grib2` (2t ensemble
members), `comfort.grib2` (Open Data 2t, 2d, 10u, 10v, sp, ssrd, ssr, strd, str at steps 6
and 12), `rad.grib2` (the same from MARS plus fdir). Blocks marked `# needs network` download
data.

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
- Thermal comfort — thermofeel
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
index, wind chill) are in `thermofeel`, not earthkit-meteo — see "Thermal comfort" below.

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

## Thermal comfort — thermofeel

Ready-made: `uv run scripts/ekplot.py indices fc.grib2 --index utci --domain Europe -o
utci.png` (maps); point series: `odpoint.py` / `ptpoint.py --indices`. thermofeel is SI in, SI
out: K, m/s, %, W m-2; temperature-like indices come back in K — convert at plot time.

Without radiation (heat index, humidex, apparent temperature, wind chill) — Open Data has
every field:

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-meteo>=1.2", "thermofeel>=2.3"]
# ///
import earthkit.data as ekd
import thermofeel as tf
from earthkit.meteo import wind

fl = ekd.from_source("file", "comfort.grib2").to_fieldlist()
x = {p: fl.sel({"parameter.variable": p, "time.step": 12})[0].to_numpy()
     for p in ("2t", "2d", "10u", "10v")}
va = wind.speed(x["10u"], x["10v"])                               # 10 m wind, m/s
rh = tf.calculate_relative_humidity_percent(x["2t"], x["2d"])     # %
heat_index = tf.calculate_heat_index_adjusted(x["2t"], x["2d"])   # K
humidex = tf.calculate_humidex(x["2t"], x["2d"])
apparent = tf.calculate_apparent_temperature(x["2t"], va, rh)    # Steadman, shade
wind_chill = tf.calculate_wind_chill(x["2t"], va)  # meaningful only -50..5 °C, 5..80 km/h
print(float(heat_index.max()), float(apparent.min()), float(wind_chill.min()))
```

UTCI, MRT and WBGT need radiation: ssrd, ssr, strd, str and **fdir** (direct solar radiation,
paramId 228021 — MARS and Polytope only, not Open Data). Radiation is accumulated from the run
start (J m-2): take the mean flux between two **consecutive** steps, and the cosine of the
solar zenith angle averaged over the same interval. Never divide by `step * 3600` (that
averages since the run start, night included); there is no interval at step 0.

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-meteo>=1.2", "earthkit-transforms[all]>=1.0",
#                 "thermofeel>=2.3"]
# ///
from datetime import timedelta

import earthkit.data as ekd
import thermofeel as tf
from earthkit.meteo import solar, wind
from earthkit.transforms import temporal

fl = ekd.from_source("file", "rad.grib2").to_fieldlist()   # MARS fields at steps 6 and 12
prev, step = 6, 12                                         # consecutive steps, never step 0
now = fl.sel({"time.step": step})
x = {f.get("parameter.variable"): f.to_numpy() for f in now}   # instantaneous at step 12
lat, lon = now[0].geography.latlons()
end = now[0].time.valid_datetime()
begin = end - timedelta(hours=step - prev)
ds = fl.sel({"parameter.variable": ["ssrd", "ssr", "strd", "str", "fdir"]}).to_xarray(
    time_dims=["valid_time"])
flux = {p: temporal.accumulation_to_rate(ds[p], accumulation_type="start_of_forecast",
                                         rate_units="seconds").sel(valid_time=end).values
        for p in ds.data_vars}                 # J m-2 -> mean W m-2 over 6-12 h
cossza = solar.cos_solar_zenith_angle_integrated(begin, end, lat, lon)  # interval mean
mrt = tf.calculate_mean_radiant_temperature(
    flux["ssrd"], flux["ssr"], tf.approximate_dsrp(flux["fdir"], cossza), flux["strd"],
    flux["fdir"], flux["str"], cossza)
va = wind.speed(x["10u"], x["10v"])
utci = tf.calculate_utci(t2_k=x["2t"], va=va, mrt=mrt, td_k=x["2d"])   # K
wbgt = tf.calculate_wbgt(x["2t"], mrt, va, x["2d"])                     # K
print(float(mrt.max()), float(utci.max()), float(wbgt.max()))
```

Fetch those fields (two consecutive steps of one run). Give `grid` as a `"dx/dy"` string —
MARS and Polytope both accept it; Polytope has rejected a list with equal values ("Duplicate
values found in list for key 'grid'"). A relative date must be the integer `-1`, not `"-1"`:

```python
# /// script
# dependencies = ["earthkit-data[mars,polytope]>=1.2"]
# ///
# needs credentials
import earthkit.data as ekd

request = {"class": "od", "stream": "oper", "type": "fc", "levtype": "sfc", "expver": "1",
           "date": -1, "time": "00", "step": "6/12", "grid": "0.25/0.25",
           "param": "167/168/165/166/134/169/176/175/177/228021"}
# 2t 2d 10u 10v sp ssrd ssr strd str fdir — fdir (228021) is what Open Data lacks
fl = ekd.from_source("mars", request).to_fieldlist()
fl = ekd.from_source("polytope", "ecmwf-mars", request, address="polytope.ecmwf.int",
                     stream=False).to_fieldlist()
```

Open Data only: estimate fdir from ssrd with `thermofeel.approximations` (Erbs, or DISC with
surface pressure). **Demonstration grade** — say so on every figure and answer:

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-meteo>=1.2", "earthkit-transforms[all]>=1.0",
#                 "earthkit-utils>=1.0", "thermofeel>=2.3"]
# ///
import earthkit.data as ekd
import thermofeel as tf
from earthkit.meteo import solar, wind
from earthkit.transforms import temporal
from earthkit.utils.units import convert_array
from thermofeel.approximations import approximate_fdir_disc, approximate_fdir_erbs

fl = ekd.from_source("file", "comfort.grib2").to_fieldlist()      # Open Data: no fdir
now = fl.sel({"time.step": 12})
x = {f.get("parameter.variable"): f.to_numpy() for f in now}
lat, lon = now[0].geography.latlons()
end = now[0].time.valid_datetime()
ds = fl.sel({"parameter.variable": ["ssrd", "ssr", "strd", "str"]}).to_xarray(
    time_dims=["valid_time"])
begin = ds.valid_time.values[0].astype("datetime64[s]").item()    # previous step
flux = {p: temporal.accumulation_to_rate(ds[p], accumulation_type="start_of_forecast",
                                         rate_units="seconds").sel(valid_time=end).values
        for p in ds.data_vars}
cossza = solar.cos_solar_zenith_angle_integrated(begin, end, lat, lon)
doy = end.timetuple().tm_yday
fdir = approximate_fdir_erbs(flux["ssrd"], cossza, doy=doy)        # estimated, W m-2
fdir_disc = approximate_fdir_disc(flux["ssrd"], cossza, doy,
                                  pressure_hpa=convert_array(x["sp"], "hPa", "Pa"))
mrt = tf.calculate_mean_radiant_temperature(
    flux["ssrd"], flux["ssr"], tf.approximate_dsrp(fdir, cossza), flux["strd"], fdir,
    flux["str"], cossza)
utci = tf.calculate_utci(t2_k=x["2t"], va=wind.speed(x["10u"], x["10v"]), mrt=mrt,
                         td_k=x["2d"])
print("UTCI, fdir estimated (Erbs) from ssrd — demonstration:", float(utci.max()))
```

WBGT after Liljegren (the physically based method KNMI uses) and KNMI's 0–10 heat force. It
takes the **fraction** of ssrd that is direct (0–1), not the fdir flux, and pressure in hPa;
interval means stand in for its instantaneous inputs — use hourly steps where possible:

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-meteo>=1.2", "earthkit-transforms[all]>=1.0",
#                 "earthkit-utils>=1.0", "thermofeel>=2.3", "numpy"]
# ///
import earthkit.data as ekd
import numpy as np
import thermofeel as tf
from earthkit.meteo import solar, wind
from earthkit.transforms import temporal
from earthkit.utils.units import convert_array

fl = ekd.from_source("file", "rad.grib2").to_fieldlist()
now = fl.sel({"time.step": 12})
x = {f.get("parameter.variable"): f.to_numpy() for f in now}
lat, lon = now[0].geography.latlons()
ds = fl.sel({"parameter.variable": ["ssrd", "fdir"]}).to_xarray(time_dims=["valid_time"])
begin, end = (t.astype("datetime64[s]").item() for t in ds.valid_time.values)
flux = {p: temporal.accumulation_to_rate(ds[p], accumulation_type="start_of_forecast",
                                         rate_units="seconds").sel(valid_time=end).values
        for p in ds.data_vars}
cossza = solar.cos_solar_zenith_angle_integrated(begin, end, lat, lon)
direct = np.divide(flux["fdir"], flux["ssrd"], out=np.zeros_like(flux["ssrd"]),
                   where=flux["ssrd"] > 0)                 # own code: direct fraction, 0-1
wbgt = tf.calculate_wbgt_liljegren(
    x["2t"], tf.calculate_relative_humidity_percent(x["2t"], x["2d"]),
    convert_array(x["sp"], "hPa", "Pa"), wind.speed(x["10u"], x["10v"]), flux["ssrd"],
    direct, cossza)                                         # K; NaN where it does not converge
force = tf.calculate_heat_force(wbgt)                      # 0-10, 2 °C WBGT bands from 14 °C
print(float(np.nanmax(wbgt)), int(np.nanmax(force)))
```

Write derived fields: GRIB2 by cloning a source field — edition 2 **first** (MARS surface
fields are GRIB1, where GRIB2 codes fail with "Concept no match"), then the paramId if
ecCodes has one (UTCI 261001, MRT 261002, WBGT 261014, humidex 261016, heat index 260004,
wind chill 260005, apparent temperature 260255), else GRIB2 local-use octets (192–254; reads
back as `unknown`). NetCDF with 2-D `latitude`/`longitude` works for regular and curvilinear
grids (CARRA, CERRA Lambert) alike:

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "thermofeel>=2.3", "xarray"]
# ///
import earthkit.data as ekd
import thermofeel as tf
import xarray as xr

fl = ekd.from_source("file", "comfort.grib2").to_fieldlist()
t2 = fl.sel({"parameter.variable": "2t", "time.step": 12})[0]
td = fl.sel({"parameter.variable": "2d", "time.step": 12})[0]
hi = tf.calculate_heat_index_adjusted(t2.to_numpy(), td.to_numpy())
di = tf.calculate_discomfort_index(
    t2.to_numpy(), tf.calculate_relative_humidity_percent(t2.to_numpy(), td.to_numpy()))
fields = [
    t2.set(values=hi).set({"metadata.edition": 2, "metadata.paramId": 260004}).sync(),
    t2.set(values=di).set({"metadata.edition": 2, "metadata.discipline": 192,  # no code:
                           "metadata.parameterCategory": 192,                  # local use
                           "metadata.parameterNumber": 192}).sync(),
]
ekd.FieldList.from_fields(fields).to_target("file", "indices.grib2")   # NaN -> bitmap
print(ekd.from_source("file", "indices.grib2").to_fieldlist().metadata("shortName"))

lat, lon = t2.geography.latlons()                # 2-D, as on a Lambert grid
ds = xr.Dataset({"heat_index": (("y", "x"), hi, {"units": "K", "long_name": "Heat index"})},
                coords={"latitude": (("y", "x"), lat, {"units": "degrees_north"}),
                        "longitude": (("y", "x"), lon, {"units": "degrees_east"})},
                attrs={"source": f"ECMWF IFS, thermofeel {tf.__version__}"})
ds.to_netcdf("indices.nc")
print(xr.open_dataset("indices.nc")["heat_index"].dims)
```

Plot a derived numpy array with earthkit-plots: pass the 2-D coordinates and the metadata it
would read from GRIB, so `units="celsius"` converts and the legend is right:

```python
# /// script
# dependencies = ["earthkit-data>=1.2", "earthkit-plots>=1.0", "thermofeel>=2.3"]
# ///
import earthkit.data as ekd
import earthkit.plots as ekp
import thermofeel as tf

fl = ekd.from_source("file", "comfort.grib2").to_fieldlist()
t2 = fl.sel({"parameter.variable": "2t", "time.step": 12})[0]
td = fl.sel({"parameter.variable": "2d", "time.step": 12})[0]
hi = tf.calculate_heat_index_adjusted(t2.to_numpy(), td.to_numpy())   # K
lat, lon = t2.geography.latlons()
style = ekp.styles.Style(levels=[0, 10, 20, 27, 32, 41, 54], colors="YlOrRd",
                         units="celsius", extend="both")   # NOAA heat-index categories
m = ekp.Map(domain="Europe")
m.contourf(hi, x=lon, y=lat, metadata={"units": "K", "long_name": "Heat index"},
           units="celsius", style=style)
m.coastlines()
m.legend(label="Heat index (°C)")
m.title(f"Heat index — {t2.time.valid_datetime():%d %b %Y %H UTC}")
m.save("heat_index.png")
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
