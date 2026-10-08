# /// script
# dependencies = ["earthkit-meteo==1.2.0", "numpy", "xarray"]
# ///
"""xarray outputs keep the first input's name and attributes (relative humidity named '2t')."""

import numpy as np
import xarray as xr
from earthkit.meteo import thermo

t = xr.DataArray(
    np.full((2, 2), 293.15),
    dims=["y", "x"],
    name="2t",
    attrs={"units": "K", "long_name": "2 metre temperature"},
)
td = t.copy(data=np.full((2, 2), 283.15)).rename("2d")
rh = thermo.relative_humidity_from_dewpoint(t, td)
print(
    "REPRODUCED:"
    if rh.name == "2t" or rh.attrs.get("long_name") == "2 metre temperature"
    else "NOT REPRODUCED:",
    rh.name,
    dict(rh.attrs),
)
