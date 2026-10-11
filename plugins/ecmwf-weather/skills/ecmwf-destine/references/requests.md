<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# DestinE Polytope requests

ECMWF's DestinE data catalogue is the source of truth for keys, values and servers:
`destine.py options` lists what exists, `destine.py request` validates against it. The tables
below are orientation. Sources: the catalogue, the official examples
(github.com/destination-earth-digital-twins/polytope-examples) and the Climate DT user guide
(platform.destine.eu/docs/climate-dt-user-guide).

## Contents
- Client call
- Catalogue
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

Collection: `destination-earth`. Server: the `address` that `destine.py request` prints.

Which collections a token may use, per data bridge:
`GET https://<bridge>/api/v1/collections` with `Authorization: Bearer <token>` →
`{"message": ["destination-earth", "ecmwf-destination-earth", "test"]}`; 401 without a valid
token. `destine.py check` queries all bridges.

## Catalogue

`https://catalogue-test.lumi.apps.dte.destination-earth.eu` (override: `DESTINE_CATALOGUE_URL`)
— public metadata about what the data bridges hold, rebuilt from them every few minutes.
`destine.py` reads `/catalogue/locations` (a selection → minimal MARS requests per location
and its Polytope URL), `/catalogue/mars-requests`, `/api/v2/axes/` (each axis's values under a
selection) and `/ai-catalogue/config` (labels). How it reads selections, all handled by
`destine.py`:

- values are matched one by one — no `to`/`by` ranges; lowercase (`ssp3-7.0`, `ifs-fesom`);
- keys that are not axes of the matched data are ignored, values without data are dropped
  silently, and an answer holds only the keys that were selected;
- Climate DT hours are stored as text (0000–0900) and numbers (1000–2300); one selection
  matches one kind only, so `destine.py` asks for the rest again and merges the answers.

## Climate DT keys

| Key | Values |
|---|---|
| `class` | `d1` |
| `dataset` | `climate-dt` |
| `generation` | `2` (current); `1` is superseded and uses different activity names |
| `activity` | `baseline` (`hist`, `cont`), `projections` (`ssp3-7.0`), `story-nudging` (storylines, IFS-FESOM: `hist`, `cont`, `tplus2.0k`; realizations 1–5) |
| `model` | `ifs-fesom`, `ifs-nemo`, `icon` |
| `realization` | `1` (storylines: 1–5) |
| `resolution` | `standard` (longer availability), `high` (limited availability) |
| `expver` | `0001` |
| `stream` | `clte` hourly (ocean daily): `date`, `time`; `clmn` monthly means: `year`, `month` |
| `type` | `fc` |
| `levtype` | `sfc`, `pl`, `o2d`, `o3d`, `hl`, `sol` |
| `param` | `clte`: `167` 2t 2 m temperature; `clmn`: time-mean parameters, `228004` avg_2t; both: `235055` avg_tprate precipitation rate |

Coverage: roughly 1990–2014 (`hist`), 2015–2049 (`ssp3-7.0`) — exact dates per model:
`destine.py options climate-dt --axis date` (hourly) or `--axis year --frequency monthly`.
Monthly mean 2 m temperature, July 2040, as the catalogue returns it (on LUMI):

```json
{"class": "d1", "dataset": "climate-dt", "activity": "projections", "experiment": "ssp3-7.0",
 "generation": "2", "model": "ifs-fesom", "realization": "1", "expver": "0001",
 "stream": "clmn", "resolution": "standard", "type": "fc", "levtype": "sfc",
 "param": "228004", "month": "7", "year": "2040"}
```

Hourly requests list every value: `"date": "20140101/20140102"`,
`"time": "0000/0100/…/2300"` (IFS-NEMO is on MN5).

## Extremes DT keys

Dates: whatever the catalogue lists (`destine.py options extremes-dt --axis date`);
`destine.py request` without `--start` takes the latest.

```json
{"class": "d1", "dataset": "extremes-dt", "expver": "0001", "stream": "oper",
 "date": "20261010", "time": "0000", "param": "167", "levtype": "sfc", "step": "0", "type": "fc"}
```

On-Demand Extremes DT (not in the catalogue): `dataset: on-demand-extremes-dt`,
`expver: 0099`, `georef: <geohash>`.

## Feature extraction

Add a `feature` block to get CoverageJSON instead of GRIB (never add `format`):
`timeseries` (Climate DT hourly `time_axis: date`, Extremes DT `time_axis: step` with a
`range`), `polygon`, `boundingbox`, `verticalprofile`, `trajectory`. Points are `[lat, lon]`
with `"axes": ["latitude", "longitude"]`. `destine.py request --lat --lon` builds a
timeseries; for monthly means request the field and extract the point with earthkit.

## Limits

At most 2 concurrent downloads per user; about 50 requests per second.

## Errors

| Error | Meaning |
|---|---|
| 401 | token missing or expired — re-run `desp-authentication.py -u <user> -o ~/.polytopeapirc-destine` |
| 403 | no upgraded access for this data — request it (`destine.py setup`) |
| no data / not found | validate with `destine.py request` first: it names the axis without data and lists the valid values |

## Discovery

- Catalogue browser: https://catalogue-test.lumi.apps.dte.destination-earth.eu/catalogue/
- Climate DT data catalogue: https://platform.destine.eu/docs/climate-dt-user-guide/doc/data/data_catalogue.html
- STAC browsers: https://catalogue.lumi.apps.dte.destination-earth.eu/ and https://catalogue.mn5.apps.dte.destination-earth.eu/
- Hosted notebooks with Polytope pre-configured: https://code.insula.destine.eu
- Support: https://platform.destine.eu/contact/
