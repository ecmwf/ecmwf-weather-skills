# /// script
# dependencies = ["earthkit-plots==1.0.4"]
# ///
"""The quickplot docstring recommends ekp.plot(), which does not exist."""

import earthkit.plots as ekp

doc = ekp.quickplot.__doc__ or ""
print(
    "REPRODUCED:" if "ekp.plot(" in doc and not hasattr(ekp, "plot") else "NOT REPRODUCED:",
    "hasattr(ekp, 'plot') =",
    hasattr(ekp, "plot"),
)
