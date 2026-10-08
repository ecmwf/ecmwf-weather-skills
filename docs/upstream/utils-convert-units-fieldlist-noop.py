# /// script
# dependencies = ["earthkit-utils==1.0.2", "earthkit-data==1.2.4"]
# ///
"""convert_units on a FieldList returns the data unchanged (only a warning is logged)."""

import earthkit.data as ekd
from earthkit.utils.units import convert_units

fl = ekd.from_source("sample", "test.grib").to_fieldlist().sel({"parameter.variable": "2t"})
before = float(fl.to_numpy().mean())
out = convert_units(fl, "degC")
after = float(out.to_numpy().mean())
print("REPRODUCED:" if abs(after - before) < 1e-6 else "NOT REPRODUCED:", before, after)
