<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)

SPDX-License-Identifier: Apache-2.0
-->

# Polytope requests

Verified against polytope.ecmwf.int; published docs and notebooks contain errors noted below.

## Contents
- Client call
- Base request and data available
- Feature schemas (timeseries, verticalprofile, polygon, boundingbox, circle, trajectory, position)
- Errors
- Destination Earth

## Client call

```python
# /// script
# dependencies = ["earthkit-data[polytope,covjsonkit]>=1.2"]
# ///
import earthkit.data as ekd
ds = ekd.from_source("polytope", "ecmwf-mars", request, stream=False, address="polytope.ecmwf.int")
ds.to_xarray()          # or json.load(open(ds.path)) for the raw CoverageJSON
```

Credentials: `POLYTOPE_USER_KEY` + `POLYTOPE_USER_EMAIL`, or `~/.polytopeapirc`
(`{"user_email": …, "user_key": …}`), else `~/.ecmwfapirc`. Without them the client prompts —
check first with `ptpoint.py --check`.

## Collections available to the account

`GET https://polytope.ecmwf.int/api/v1/collections` with `Authorization: EmailKey <email>:<key>`
(or `Bearer <key>`) returns `{"message": ["cems", "ecmwf-mars", ...]}` — the collections this
account may use. Without credentials it answers 401. `ptpoint.py --check` does this.

## Base request and data available

```python
BASE = {"class": "od", "stream": "oper", "type": "fc", "date": -1, "time": "0000",
        "levtype": "sfc", "expver": "0001", "domain": "g", "param": "167/228"}
# ensemble: "stream": "enfo", "type": "pf", "number": "1/to/50"
```

- `ecmwf-mars`, `class: od`: operational IFS HRES (`oper`/`fc`) and ENS (`enfo`/`pf`), roughly
  the last 2 days; `date` is `YYYYMMDD` or a negative offset (`-1` = yesterday). Surface
  parameters by paramId: 167 2t, 168 2d, 228 tp, 165/166 10u/10v, 49 10fg, 151 msl, 164 tcc,
  134 sp, 169 ssrd.
- `class: ai`, `model: aifs-single`, `stream: oper`, `type: fc`: the AIFS Open Data section
  (~3–5 days, CC BY 4.0).
- Pressure/model levels on operational data list only a few parameters (o3, q, cc, clwc, ciwc,
  pv) — check before promising temperature/wind profiles.
- Omitting `feature` returns full GRIB fields (use the `ecmwf-mars`/`ecmwf-open-data` routes for that instead).

## Feature schemas

Never add `"format": "covjson"`. Points are `[lat, lon]`.

**timeseries** — `points`, `time_axis` required; give the time range either as `range` or as
`step` in the body, not both.
```python
{**BASE, "feature": {"type": "timeseries", "points": [[38.72, -9.14]], "axes": ["latitude", "longitude"],
                     "time_axis": "step", "range": {"start": 0, "end": 240}}}
```

**verticalprofile** — `points`; levels via `range` over `levelist` or `levelist` in the body.
```python
{**BASE, "levtype": "pl", "param": "133", "step": "0",
 "feature": {"type": "verticalprofile", "points": [[38.9, -9.1]], "axes": "levelist",
             "range": {"start": 0, "end": 1000}}}
```

**polygon** — `shape`: list of `[lat, lon]` vertices (≤ 1000), ring closed automatically.
```python
{**BASE, "step": "0", "feature": {"type": "polygon",
 "shape": [[42.2, -9.5], [42.2, -6.2], [36.9, -7.4], [36.9, -9.5]]}}
```

**boundingbox** — two corners; use `[[lat_min, lon_min], [lat_max, lon_max]]` (docs disagree on
order). Large areas may be refused.
```python
{**BASE, "step": "0", "feature": {"type": "boundingbox", "points": [[36.9, -9.5], [42.2, -6.2]]}}
```

**circle** — `center` nested `[[lat, lon]]`, `radius` in **degrees**.
```python
{**BASE, "step": "0", "feature": {"type": "circle", "center": [[38.72, -9.14]], "radius": 0.5}}
```

**trajectory** — `points` (≥ 2), `inflation` (degrees), `inflate` `round`|`box`; always pass
`axes` (the code default is 4-D).
```python
{**BASE, "step": "0", "feature": {"type": "trajectory", "points": [[38.7, -9.1], [40.4, -3.7], [41.4, 2.2]],
 "inflation": 0.1, "inflate": "round", "axes": ["latitude", "longitude"]}}
```

**position** — several points at a fixed `step`/`date`.
```python
{**BASE, "step": "24", "feature": {"type": "position", "points": [[38.72, -9.14], [51.5, -0.1]]}}
```

## Errors

| Message contains | Meaning / fix |
|---|---|
| `TypeEnum[name=format]: cannot expand 'covjson'` | remove `"format"` |
| `overspecified` / `underspecified` | give the time (or level) axis once: `range` **or** body key |
| `Number of coordinates*fields … exceeds` | fewer points, params or steps per request |
| 401 / 403 | no Polytope access for this account → ecmwf-open-data skill |
| no data / not found for a date | run not yet available or older than ~2 days → try the previous run |

## Destination Earth

Address `polytope.lumi.apps.dte.destination-earth.eu` (or `…mn5…`), collection
`destination-earth`, `class: d1`, `dataset: climate-dt` or `extremes-dt` (last ~15 days). Climate
DT keys: `activity`, `experiment`, `generation`, `model`, `realization`, `resolution`, `stream`
(`clte` hourly, `clmn` monthly); values differ between generations — check the DestinE catalogue
(https://catalogue.lumi.apps.dte.destination-earth.eu/). Auth: DESP account →
`desp-authentication.py` (github.com/destination-earth-digital-twins/polytope-examples) writes an
offline token to `~/.polytopeapirc` — **overwriting** an ECMWF key there; keep it in a separate
file and point `POLYTOPE_CONFIG_PATH` or `user_key=` at it. Full Destination Earth support is
planned as its own skill.
