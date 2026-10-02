# Research — earthkit components (2026-10-02)

Method: PyPI JSON API, `pip install --dry-run --report` for dependency sets, wheel HEAD requests
for sizes (Linux x86_64, Py3.12), unpacked-wheel source reading, and a live end-to-end run
(open-data fetch → select → xarray → wind speed → regrid → nearest point → daily max → units →
country reduction → map + timeseries plot). ✅ = executed, ⚠️ = source-read only.

Re-run this research before each minor release (see PLAN.md §0).

## Eligible (≥ 1.0) — use these

| Package | Version | Purpose | Download |
|---|---|---|---|
| `earthkit-data` | 1.2.3 | Read/fetch/convert GRIB, NetCDF, BUFR…; sources: file, url, ecmwf-open-data, cds, ads, mars, polytope | 64 MB (eccodes binary bundled) |
| `earthkit-geo` | 1.1.2 | Regridding (MIR), nearest point, distances, rotation, GISCO country/NUTS polygons | 90 MB |
| `earthkit-meteo` | 1.2.0 | Wind, thermo (RH, dewpoint, θ, wet bulb), vertical, solar, EFI/SOT, scores, stats | 18 MB |
| `earthkit-plots` | 1.0.4 | Maps (`quickplot`, `geo.*`) and time series (`timeseries.*`) | 159 MB |
| `earthkit-transforms[all]` | 1.0.0 | Temporal/spatial/climatology/ensemble aggregation | 150 MB (base 30 MB but **import fails without `[all]`**) |
| `earthkit-utils` | 1.0.2 | Unit conversion (`units.convert_units`), array backends | 1 MB |
| `earthkit-hydro` | 1.4.0 | River networks — out of scope for now | 31 MB |
| `thermofeel` (not earthkit) | 2.3.0 | UTCI, heat index, wind chill, humidex, apparent temp, WBGT | tiny |

## Excluded (< 1.0 or deprecated)

`earthkit-regrid` 0.5.1 (deprecated → earthkit-geo), `earthkit-time` 0.1.8, `earthkit-climate` 0.3.2
and `earthkit-workflows` 0.18.0 (both require `earthkit-data<1` — conflict), `earthkit-maps`
(deprecated → plots), `earthkit-aggregate` (deprecated → transforms), `earthkit-plots-default-styles`
0.1.3. Meta-package `earthkit` 1.0.0 is ≥1.0 but pulls everything (112 pkgs, ~231 MB) — **do not use**.

## Function → component

| Function | Component | API |
|---|---|---|
| Read GRIB/NetCDF | earthkit-data | `ekd.from_source("file", p).to_fieldlist()` / `.to_xarray()` ✅ |
| Open Data fetch | earthkit-data`[ecmwf-opendata]` | `from_source("ecmwf-open-data", param=…, step=…, levtype="sfc", source="ecmwf"\|"aws"\|"google"\|"azure", model="ifs")`; omit `date` → latest ✅ |
| CDS / ADS | earthkit-data`[cds]` | `from_source("cds"\|"ads", dataset, request)` ⚠️ |
| MARS | earthkit-data`[mars]` | `from_source("mars", request)` ⚠️ |
| Polytope | earthkit-data`[polytope]` | `from_source("polytope", "ecmwf-mars", request, …)` ⚠️ |
| Select | earthkit-data | `fl.sel({"metadata.shortName": "2t"})` ✅ — **not** `sel(param=…)` (silently empty in 1.2.3) |
| Time keys | earthkit-data | `f.time.base_datetime()/valid_datetime()/step()` ✅ |
| xarray/pandas | earthkit-data | `to_xarray(time_dims=["valid_time"])` ✅ |
| Nearest gridpoint | earthkit-geo | `nearest_point_kdtree((lat,lon), f.geography.latlons())` ✅; not `to_pandas(latitude=…)` (empty) |
| Regrid | earthkit-geo | `earthkit.geo.regrid(fl, out_grid={"grid":[1,1]}, interpolation="linear")` ✅ (MIR cache → set writable dir) |
| Wind speed/dir | earthkit-meteo | `wind.speed(u,v)` ✅, `wind.direction(u,v,convention="meteo")` |
| RH / dewpoint | earthkit-meteo | `thermo.relative_humidity_from_dewpoint(t, td)` |
| Thermal indices | thermofeel | `calculate_utci`, `calculate_heat_index_adjusted`, … |
| Units | earthkit-utils | `units.convert_units(x, "degC")` ✅ |
| Temporal aggregation | earthkit-transforms[all] | `temporal.daily_max(da, time_dim="valid_time")` ✅, `deaccumulate` |
| Polygon/country | earthkit-transforms[all] + earthkit-geo | `gisco.countries()` → `spatial.reduce(da, gdf, how="mean")` ✅ |
| Maps | earthkit-plots | `ekp.quickplot(data, domain="Europe")` ✅ — **`ekp.plot` does not exist** |
| Meteogram | earthkit-plots | no helper — compose `ekp.timeseries.line` / `multiboxplot` / `fill_between` ✅ |

## Install bundles (task-scoped, never all at once)

| Bundle | Spec | ≈ Download |
|---|---|---|
| fetch-opendata | `earthkit-data[ecmwf-opendata]>=1.2` | 64 MB |
| point | fetch-opendata + `earthkit-geo>=1.1` + `earthkit-utils>=1` | ~150 MB |
| derive | `earthkit-meteo>=1.2` (+ `thermofeel>=2`) | 18 MB |
| plot | `earthkit-plots>=1.0` (pulls earthkit-data, cartopy) | 159 MB |
| aggregate | `earthkit-transforms[all]>=1.0` | 150 MB |
| cds / mars / polytope | `earthkit-data[cds]` / `[mars]` / `[polytope]` | ~64 MB each |

## Platform caveats

- eccodes/eckit/MIR come as binary wheels — Linux x86_64/aarch64, macOS 13+. **No Windows wheels**:
  pip silently resolves earthkit-data 0.20 (pre-1.0 API). Always pin `>=1` so it fails loudly;
  stdlib fallback on Windows.
- Missing CDS/ADS/MARS credentials trigger an interactive `getpass` prompt — always pre-check
  credentials (CDS/MARS accept `prompt=False`; ADS does not).
- ADS credentials: only `~/.adsapirc` (no env vars in earthkit path).
