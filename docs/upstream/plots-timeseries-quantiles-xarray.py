# /// script
# dependencies = ["earthkit-plots==1.0.4", "numpy", "xarray"]
# ///
"""TimeSeries.quantiles fails on an ensemble (member x time) DataArray."""

import earthkit.plots as ekp
import numpy as np
import xarray as xr

t = np.arange("2026-10-03T00", "2026-10-04T00", 6, dtype="datetime64[h]").astype("datetime64[ns]")
da = xr.DataArray(
    np.random.default_rng(0).normal(0, 1, (10, t.size)),
    dims=["member", "time"],
    coords={"time": t},
    name="x",
    attrs={"units": "K"},
)
fig = ekp.Figure(rows=1, columns=1)
ts = fig.add_timeseries()
try:
    ts.quantiles(da)
    print("NOT REPRODUCED")
except Exception as e:
    print("REPRODUCED:", type(e).__name__, str(e)[:200])
