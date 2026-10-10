<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# earthkit 1.x pitfalls and known issues

Verified against earthkit-data 1.2.4, earthkit-plots 1.0.4, earthkit-meteo 1.2.0,
earthkit-geo 1.1.2, earthkit-transforms 1.0.0, earthkit-utils 1.0.2.

## Contents
- 0.x habits that break on 1.x
- Plotting
- Statistics and transforms
- Meteorology
- Thermal comfort (thermofeel)
- Regional grids (CARRA, CERRA)
- Environment

## 0.x habits that break on 1.x

| Habit | 1.x reality | Instead |
|---|---|---|
| `fl.sel(param="2t")`, `sel(shortName=)` | returns 0 fields, no error | `fl.sel({"parameter.variable": "2t"})` |
| iterate `from_source(...)` | a lazy source, not a FieldList | `.to_fieldlist()` / `.to_xarray()` |
| `f.metadata("level", default=None)` | `TypeError` | `f.get("vertical.level", default=None)` |
| `fl1 + fl2` to concatenate | arithmetic | `ekd.concat(fl1, fl2)` |
| `fl.isel(...)` | does not exist | slice `fl[2:5]` |
| `to_xarray()` for forecasts | `step` axis; temporal statistics fail | `to_xarray(time_dims=["valid_time"])` |
| ensemble dim `number` | xarray dim is `member`, string values | `.sel(member="1")` |
| `import earthkit.regrid` | deprecated | `earthkit.geo.regrid` |
| `pip install earthkit` | ~230 MB | the components you need |

## Plotting

- `ekp.plot` does not exist; use `ekp.geo.plot` or `ekp.quickplot`.
- `style="auto"` is the default for `ekp.geo.plot`/`quickplot`, **not** for `Map.contourf` —
  pass it, or you get a generic palette.
- Derived fields keep the source metadata: never `style="auto"` or `units="celsius"` on a spread
  or difference (the −273.15 offset would be applied); give levels, colours and a legend label.
- Pass a FieldList (`.to_fieldlist()`, `.sel()`); a raw `from_source` object plots only its
  first field.
- Multi-member time series: one `line` per member; a 2-D member × time array picks the wrong
  x-axis. `TimeSeries.quantiles` fails on xarray — use `fill_between` / `envelope`.
- `spaghetti` accepts FieldLists only (not xarray).

## Statistics and transforms

- `earthkit-transforms` temporal functions need a datetime dimension (`time_dims=["valid_time"]`).
- `accumulation_to_rate` defaults to `start_of_step`; IFS `tp` is `start_of_forecast`.
- `ekt.reduce(..., how="percentile", q=...)` takes one `q` at a time.
- `spatial.reduce` returns NaN for western-hemisphere polygons on 0–360 grids: shift longitudes
  to −180…180 first. `return_as="pandas"` crashes.
- `climatology_range` must be strings: `("1991", "2020")`.
- `earthkit.utils.units.convert_units` on a FieldList does nothing; convert xarray, or use
  `units=` when plotting.

## Meteorology

- `thermo.wet_bulb_temperature_from_dewpoint` with the default `t_method="bisect"` fails on 2-D
  or 3-D input — use `t_method="newton"`.
- xarray results keep the input's name and attributes (relative humidity named `2t`); rename.
- Plain Python floats are rejected; wrap in `np.array`. Dates are `datetime`, not
  `np.datetime64`.
- Scores need `earthkit-meteo[scores]`; the library's error message says `[score]`, which does
  not exist.
- EFI/SOT on xarray need `clim_dim=` and `ens_dim=`.

## Thermal comfort (thermofeel)

- **fdir is not in Open Data.** UTCI, MRT and WBGT need direct solar radiation (fdir,
  paramId 228021) from MARS or Polytope. Estimating it from ssrd
  (`thermofeel.approximations.approximate_fdir_erbs` / `_disc`) is demonstration grade —
  label every result that uses it.
- **Radiation is accumulated from the run start** (J m-2). Mean flux = difference between
  consecutive steps ÷ the interval (`accumulation_to_rate(..., "start_of_forecast",
  rate_units="seconds")`), with `solar.cos_solar_zenith_angle_integrated(begin, end, ...)`
  over the same interval — it returns the interval mean in earthkit-meteo 1.x. Dividing by
  `step * 3600` averages since the run start (night included). Step 0 has no interval: skip it.
- thermofeel is SI in, SI out (K, m/s at 10 m, %, W m-2, hPa only for Liljegren pressure);
  indices come back in K — convert with `units="celsius"` when plotting.
- `calculate_wbgt_liljegren` takes the direct **fraction** of ssrd (0–1), not the fdir flux.
- Validity: UTCI fit −50…50 °C air temperature, 0.5–17 m/s wind, MRT up to 70 K above air
  temperature (it extrapolates outside, e.g. over Antarctica); wind chill −50…5 °C and
  5–80 km/h; heat index meaningful above ~27 °C, humidex above ~20 °C.
- PMV/PPD want the air speed at the body: the 10 m wind is only a coarse proxy — say so.
- Writing GRIB: set `metadata.edition: 2` **before** a GRIB2 paramId (GRIB1 templates from
  MARS raise "Concept no match"); indices without a code go in local-use octets (192–254).
- Requests: give `grid` as a `"dx/dy"` string (Polytope has rejected equal-valued lists:
  "Duplicate values found in list for key 'grid'"); the mars source rejects `date="-1"` —
  use the integer `-1` or an ISO date.

## Regional grids (CARRA, CERRA)

- Lambert conformal fields: `to_xarray()` gives `y`/`x` dimensions with 2-D `latitude` and
  `longitude`; `.sel(latitude=slice(...))` does not work. Crop with a mask on the 2-D
  coordinates, or `earthkit.geo.regrid(fl, out_grid={"grid": [0.05, 0.05]})` first (points
  outside the domain become missing), then select.
- Wind components on the native grid follow the grid axes, not true north.

## Environment

- eccodes, eckit and MIR ship as wheels (Linux, macOS). **No Windows wheels**: pip quietly
  resolves earthkit-data 0.x — pin `>=1`; use WSL.
- Licence banners print to stdout — `contextlib.redirect_stdout(sys.stderr)` when emitting JSON.
- CDS/ADS/MARS sources prompt for missing credentials — check credentials first.
- MIR caches interpolation weights — set `MIR_CACHE_PATH` to a writable directory.
