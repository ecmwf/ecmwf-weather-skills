# /// script
# dependencies = ["earthkit-meteo==1.2.0", "numpy"]
# ///
"""wet_bulb_temperature_from_dewpoint: the default t_method="bisect" fails on 2-D input."""

import numpy as np
from earthkit.meteo import thermo

t, td, p = (np.full((3, 4), v) for v in (293.15, 283.15, 101325.0))
print("newton:", thermo.wet_bulb_temperature_from_dewpoint(t, td, p, t_method="newton").shape)
try:
    thermo.wet_bulb_temperature_from_dewpoint(t, td, p)
    print("NOT REPRODUCED")
except Exception as e:
    print("REPRODUCED:", type(e).__name__, str(e)[:200])
