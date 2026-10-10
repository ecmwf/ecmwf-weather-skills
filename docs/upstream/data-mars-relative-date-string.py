# /// script
# dependencies = ["earthkit-data[mars]==1.2.4"]
# ///
"""The mars source rejects date="-1" (a MARS relative date, as a string); the integer -1 works."""

import earthkit.data as ekd

request = {
    "class": "od",
    "stream": "oper",
    "type": "fc",
    "levtype": "sfc",
    "param": "2t",
    "date": "-1",
    "time": "00",
    "step": "0",
    "grid": "5/5",
}
try:
    ekd.from_source("mars", request, prompt=False)
    print("NOT REPRODUCED: date='-1' accepted")
except ValueError as e:
    print("REPRODUCED:" if "Invalid datetime" in str(e) else "NOT REPRODUCED:", e)
