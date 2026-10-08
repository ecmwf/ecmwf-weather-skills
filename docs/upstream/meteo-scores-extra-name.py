# /// script
# dependencies = ["earthkit-meteo==1.2.0", "numpy", "xarray"]
# ///
"""Without the scores extra, the error tells users to install `[score]`, which doesn't exist."""

import numpy as np
import xarray as xr
from earthkit.meteo import score

ens = xr.DataArray(np.random.default_rng(0).normal(size=(5, 3)), dims=["member", "x"])
try:
    score.crps_from_ensemble(ens, ens.mean("member"), over="member")
    print("NOT REPRODUCED (scores installed?)")
except RuntimeError as e:
    print("REPRODUCED:" if "[score]" in str(e) else "NOT REPRODUCED:", str(e)[:200])
