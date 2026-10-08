# /// script
# dependencies = ["earthkit-transforms[all]==1.0.0", "numpy", "xarray"]
# ///
"""spatial.reduce(..., return_as="pandas") crashes."""

import numpy as np
import xarray as xr
from earthkit.transforms import spatial

lat, lon = np.arange(60.0, 29.0, -5.0), np.arange(-20.0, 41.0, 5.0)
da = xr.DataArray(
    np.ones((lat.size, lon.size)),
    dims=["latitude", "longitude"],
    coords={"latitude": lat, "longitude": lon},
    name="x",
)
area = {"north": 55, "south": 35, "west": -10, "east": 30}
print("xarray ok:", spatial.reduce(da, area=area).values.ravel()[:3])
try:
    spatial.reduce(da, area=area, return_as="pandas")
    print("NOT REPRODUCED")
except Exception as e:
    print("REPRODUCED:", type(e).__name__, str(e)[:200])
