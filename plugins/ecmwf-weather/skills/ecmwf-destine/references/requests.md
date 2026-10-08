<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# DestinE Polytope requests

From the official examples (github.com/destination-earth-digital-twins/polytope-examples) and
the Climate DT user guide (platform.destine.eu/docs/climate-dt-user-guide). Not yet verified
with a live request by this project.

## Contents
- Client call
- Climate DT keys
- Extremes DT keys
- Feature extraction
- Limits
- Errors
- Discovery

## Client call

```python
# /// script
# dependencies = ["polytope-client>=0.7"]
# ///
# needs credentials (not run by the tests)
import json, pathlib
from polytope.api import Client
token = json.loads(pathlib.Path("~/.polytopeapirc-destine").expanduser().read_text())["user_key"]
Client(address="polytope.lumi.apps.dte.destination-earth.eu", user_key=token, quiet=True) \
    .retrieve("destination-earth", request, "out.grib")
```

Collection: `destination-earth`. Server: see SKILL.md (or `destine.py request`).

Which collections a token may use, per data bridge:
`GET https://<bridge>/api/v1/collections` with `Authorization: Bearer <token>` →
`{"message": ["destination-earth", "ecmwf-destination-earth", "test"]}`; 401 without a valid
token. `destine.py check` queries all bridges.

## Climate DT keys

| Key | Values |
|---|---|
| `class` | `d1` |
| `dataset` | `climate-dt` |
| `generation` | `2` (current); `1` is superseded and uses different activity names |
| `activity` | `baseline`, `projections`, `story-nudging` |
| `experiment` | `cont` (control), `hist` (historical), `SSP3-7.0`, `Tplus2.0K` |
| `model` | `ifs-fesom`, `ifs-nemo`, `icon` |
| `realization` | `1` (storylines: 1–5) |
| `resolution` | `high` or `standard` |
| `expver` | `0001` |
| `stream` | `clte` hourly (ocean daily), `clmn` monthly (use `year`/`month` instead of `date`/`time`) |
| `type` | `fc` |
| `levtype` | `sfc`, `pl`, `o2d`, … |
| `date`, `time` | `20140101/to/20140131`, `0000/to/2300` |
| `param` | paramId, e.g. `167` 2 m temperature, `228` total precipitation |

Coverage: roughly 1990–2049. Example (historical IFS-NEMO, January 2014, on MN5):

```json
{"class": "d1", "dataset": "climate-dt", "activity": "baseline", "experiment": "hist",
 "generation": "2", "model": "ifs-nemo", "realization": "1", "resolution": "high",
 "expver": "0001", "stream": "clte", "type": "fc", "levtype": "sfc",
 "date": "20140101/to/20140131", "time": "0000/to/2300", "param": "167"}
```

## Extremes DT keys

Rolling window of roughly the last 15 start dates — use relative dates (`-1`, `-14`).

```json
{"class": "d1", "dataset": "extremes-dt", "expver": "0001", "stream": "oper", "type": "fc",
 "date": "-1", "time": "0000", "levtype": "sfc", "step": "0", "param": "167"}
```

On-Demand Extremes DT: `dataset: on-demand-extremes-dt`, `expver: 0099`, `georef: <geohash>`.

## Feature extraction

Add a `feature` block to get CoverageJSON instead of GRIB (never add `format`):
`timeseries` (Climate DT `time_axis: date`, Extremes DT `time_axis: step` with a `range`),
`polygon`, `boundingbox`, `verticalprofile`, `trajectory`. Points are `[lat, lon]` with
`"axes": ["latitude", "longitude"]`. `destine.py request --lat --lon` builds a timeseries.

## Limits

At most 2 concurrent downloads per user; about 50 requests per second.

## Errors

| Error | Meaning |
|---|---|
| 401 | token missing or expired — re-run `desp-authentication.py -u <user> -o ~/.polytopeapirc-destine` |
| 403 | no upgraded access for this data — request it (`destine.py setup`) |
| no data / not found | wrong server for that model/generation, or date outside the archive / rolling window |

## Discovery

- Climate DT data catalogue: https://platform.destine.eu/docs/climate-dt-user-guide/doc/data/data_catalogue.html
- STAC browsers: https://catalogue.lumi.apps.dte.destination-earth.eu/ and https://catalogue.mn5.apps.dte.destination-earth.eu/
- Hosted notebooks with Polytope pre-configured: https://code.insula.destine.eu
- Support: https://platform.destine.eu/contact/
