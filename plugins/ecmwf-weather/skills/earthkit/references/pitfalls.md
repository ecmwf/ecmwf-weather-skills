# earthkit 1.x pitfalls (verified against earthkit-data 1.2.3, plots 1.0.4)

Most earthkit examples online target 0.x. These differences break silently or loudly:

| 0.x habit | 1.x reality | Fix |
|---|---|---|
| `ds.sel(param="2t")` | returns 0 fields, no error | `fl.sel({"metadata.shortName": "2t"})` |
| `f.metadata("level", default=None)` | `TypeError` — no `default` | `f.get("metadata.level", default=None)` |
| `ds = from_source(...)` then iterate | returns a source/data object | `.to_fieldlist()` / `.to_xarray()` first |
| `ds.to_xarray()` for forecasts | `step` dimension, aggregations fail | `to_xarray(time_dims=["valid_time"])` |
| `ekp.plot(...)` | does not exist | `ekp.quickplot(...)` or `ekp.geo.plot(...)` |
| `ekp.Figure(size=…)` | deprecated | `figsize=…` |
| `import earthkit.regrid` | deprecated package | `earthkit.geo.regrid` |
| `pip install earthkit-transforms` | import fails without geopandas | `earthkit-transforms[all]` |
| `pip install earthkit` | ~230 MB, 112 packages | install components individually |
| `to_pandas(latitude=, longitude=)` on grids | empty frame | `earthkit.geo.distance.nearest_point_kdtree` |

Environment:

- eccodes, eckit and MIR ship as binary wheels (Linux x86_64/aarch64, macOS 13+, Python
  3.10–3.14). **No Windows wheels**: pip quietly resolves earthkit-data 0.20 — always pin `>=1`.
- Licence banners and connection-limit notices print to **stdout** — wrap fetches in
  `contextlib.redirect_stdout(sys.stderr)` when emitting JSON.
- CDS/ADS/MARS sources prompt interactively for missing credentials (`getpass`) — pre-check
  credentials; pass `prompt=False` where supported.
- MIR writes interpolation weights to a cache directory — set `MIR_CACHE_PATH` to somewhere
  writable.
