# /// script
# dependencies = ["earthkit-plots==1.0.4", "numpy", "xarray"]
# ///
"""geo.spaghetti documents xarray input but rejects an ensemble DataArray; the error message
also prints an unformatted placeholder ({self.selection.shape} — missing f-string prefix)."""

import earthkit.plots as ekp
import numpy as np
import xarray as xr

lat, lon = np.linspace(80, 20, 13), np.linspace(-40, 40, 17)
da = xr.DataArray(
    270 + np.random.default_rng(0).normal(0, 3, (5, 13, 17)),
    dims=["member", "latitude", "longitude"],
    coords={"latitude": lat, "longitude": lon},
    name="2t",
    attrs={"units": "K"},
)
print("documents xarray:", "xarray" in (ekp.geo.spaghetti.__doc__ or ""))
try:
    ekp.geo.spaghetti(da, levels=[273.15])
    print("NOT REPRODUCED")
except Exception as e:
    print("REPRODUCED:", type(e).__name__, str(e)[:200])
