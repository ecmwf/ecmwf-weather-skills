---
name: polytope
description: Extracts just the data needed from ECMWF's operational forecasts with Polytope feature extraction — hourly point time series (meteograms), 50-member ensemble percentiles at a point, vertical profiles, polygons, bounding boxes, circles and trajectories — in kilobytes instead of downloading global fields. Use when the user has ECMWF credentials (POLYTOPE_USER_KEY, ~/.polytopeapirc or ~/.ecmwfapirc) and asks for a forecast or meteogram at a place, forecast uncertainty, ensemble spread, percentiles or likely ranges at a location (preferred over open-data for any ensemble question at a point), weather along a route or inside a region, Polytope requests or errors, the earthkit polytope source, or whether they have Polytope access. Falls back to the open-data skill when there are no credentials.
compatibility: scripts/ptpoint.py needs uv (PEP 723 inline dependencies — earthkit-data[polytope,covjsonkit], earthkit-meteo, earthkit-utils) and network access to polytope.ecmwf.int. `--check` without credentials runs on plain Python 3.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.4"
---

<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# ECMWF Polytope

## Contents
- Access check (always first)
- Point forecast and ensemble workflow
- Other features (profiles, areas, routes)
- Licence and attribution (operational data is not CC BY)
- References — `references/requests.md` (request keywords, every feature schema, data available, Destination Earth)

Polytope cuts features out of ECMWF's datacubes server-side: a 10-day hourly point forecast is
~10 KB and takes ~2 s, versus ~300 MB from Open Data. Access is for users at ECMWF Member and
Co-operating States' national services (ECMWF key from https://api.ecmwf.int/v1/key/) and
Destination Earth accounts. Operational data covers roughly the last 2 days of runs.

Use ECMWF sources only — never substitute a third-party weather API; if no ECMWF route works,
say so.

## Access check

Run it yourself — don't ask the user to run it — and report the result.

```bash
uv run scripts/ptpoint.py --check --json     # never prints keys
```

- exit 0, `"verified": true` → use Polytope.
- exit 4 → no credentials or no access: use the `open-data` skill (`odpoint.py`), say once that
  Polytope access would make it hourly, native resolution and ~10 KB, and ask whether they'd
  like instructions to obtain it; if yes, relay `uv run scripts/ptpoint.py --setup` as printed.
- Destination Earth Digital Twin data is a different service and account: the `destine` skill.

## Point forecast and ensemble workflow

Run the scripts; don't read them. Run them from the user's working directory and write
files there, never inside the skill directory.

```
- [ ] 1. Coordinates for the place (ask if ambiguous)
- [ ] 2. uv run scripts/ptpoint.py --lat LAT --lon LON --next-hours 48 --tz ZONE --json > point.json
         (add --ensemble for uncertainty: 50 members -> p10/p50/p90 of 2 m temperature, precipitation)
- [ ] 3. Check "series" is non-empty; exit 4 -> open-data skill; exit 2 -> read the error
- [ ] 4. Optional image: the earthkit skill's ekplot.py meteogram point.json -o meteogram.png
         (ensemble output is drawn as median + shaded 10-90 % range)
- [ ] 5. Answer in local time; give ensemble ranges as "most likely X, range Y-Z";
         end with the attribution line from the output
```

- Output (UTC, hourly to 90 h, then 3-hourly/6-hourly): `t2m_C`, `precip_mm` (since previous
  step), `wind_speed_ms`, `wind_dir_deg`, `msl_hPa`, `tcc_pct`; ensemble adds `*_p10/_p50/_p90`
  and `members`.
- `--next-hours N` returns exactly the next N hours from now (the run started hours ago, so
  `--steps 0-N` covers less); `--tz ZONE` adds `local_time`; `--from-now` drops past rows; `--daily` adds per-local-day high,
  low and precipitation — for `--ensemble` as p10/p50/p90 of each member's daily value, which
  is the right way to state "likely range per day". Don't aggregate yourself.
- The script picks the latest run covering the range (06/18 UTC runs reach 144 h, 00/12 UTC
  240 h+); `--date YYYYMMDD --time HHMM` pins one.

## Other features

For profiles, polygons, bounding boxes, circles and routes, write the request with
`earthkit.data.from_source("polytope", "ecmwf-mars", request, stream=False,
address="polytope.ecmwf.int")` — schemas and working examples in `references/requests.md`.
Two rules the server enforces that most published examples get wrong:

- **Omit `"format": "covjson"`** — the server rejects it; CoverageJSON comes back anyway.
- **Points are `[lat, lon]`** with `"axes": ["latitude", "longitude"]`.

## Licence and attribution

Operational data (`class: od`) is **not** open data: it is used under the user's organisation's
ECMWF licence. Show `Data: © <year> ECMWF` and warn before redistributing or publishing. The
Open Data section (`class: ai`, AIFS) is CC BY 4.0. `ptpoint.py` reports which applies in
`attribution`, and `ekplot.py` stamps that line on figures.
