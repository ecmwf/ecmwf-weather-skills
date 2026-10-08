# /// script
# dependencies = ["earthkit-transforms[all]==1.0.0", "numpy", "xarray", "shapely", "geopandas"]
# ///
"""spatial.reduce returns NaN for a western-hemisphere polygon when longitudes run 0-360."""

import geopandas as gpd
import numpy as np
import xarray as xr
from earthkit.transforms import spatial
from shapely.geometry import box

lat, lon = np.arange(60.0, -61.0, -5.0), np.arange(0.0, 360.0, 5.0)
da = xr.DataArray(
    np.ones((lat.size, lon.size)),
    dims=["latitude", "longitude"],
    coords={"latitude": lat, "longitude": lon},
)
west = gpd.GeoDataFrame({"id": ["west"]}, geometry=[box(-70, -30, -40, 0)], crs="EPSG:4326")
v360 = float(spatial.reduce(da, west, mask_dim="id").squeeze())
shifted = da.assign_coords(longitude=((da.longitude + 180) % 360) - 180).sortby("longitude")
v180 = float(spatial.reduce(shifted, west, mask_dim="id").squeeze())
print("REPRODUCED:" if np.isnan(v360) and v180 == 1.0 else "NOT REPRODUCED:", v360, v180)
