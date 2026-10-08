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

## Environment

- eccodes, eckit and MIR ship as wheels (Linux, macOS). **No Windows wheels**: pip quietly
  resolves earthkit-data 0.x — pin `>=1`; use WSL.
- Licence banners print to stdout — `contextlib.redirect_stdout(sys.stderr)` when emitting JSON.
- CDS/ADS/MARS sources prompt for missing credentials — check credentials first.
- MIR caches interpolation weights — set `MIR_CACHE_PATH` to a writable directory.
