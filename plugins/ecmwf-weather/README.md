<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# ECMWF Weather Data

Eight skills that let an AI agent find, fetch, decode, map and cite data from the European Centre
for Medium-Range Weather Forecasts (ECMWF): real-time IFS and AIFS forecasts (Open Data), ERA5
reanalysis and CAMS air quality (Copernicus data stores), the MARS archive and its station
observations, Polytope point and
area extraction, Destination Earth Digital Twins, ecCharts WMS / OpenCharts maps, and decoding and
plotting with ECMWF's earthkit. Source, documentation and issues:
https://github.com/ecmwf/ecmwf-weather-skills

## Skills

| Skill | What it does |
|---|---|
| `ecmwf-open-data` | Latest runs, fields, download sizes and point forecasts from free ECMWF Open Data |
| `ecmwf-earthkit` | Inspect, process and plot GRIB/NetCDF files; maps and meteograms |
| `ecmwf-opencharts-wms` | WMS map layers and images, official ECMWF charts, a local interactive web map |
| `ecmwf-polytope` | Point, ensemble and area extraction for authenticated ECMWF users |
| `ecmwf-cds-ads` | Find, validate, cost and download ERA5, CAMS and ECMWF Data Store datasets |
| `ecmwf-mars` | Check, size, plan and retrieve requests from the ECMWF MARS archive |
| `ecmwf-destine` | Destination Earth Digital Twin data via DestinE Polytope (upgraded access on request) |
| `ecmwf-observations` | Station observations from MARS, forecast verification, observed thermal indices |

## What runs, and what it sends

Each skill runs small Python helper scripts from its `scripts/` folder. Scripts that decode GRIB
declare their Python dependencies inline and are run with `uv`, which installs them from PyPI
on first use (earthkit components, eccodes, polytope-client, ecmwf-api-client, cdsapi, Pillow).
Nothing is installed globally and nothing runs in the background, except the optional local web
map server (`webmap.py serve`), which listens on 127.0.0.1 only.

The scripts contact only these services, and only to fetch data, catalogues and status:

| Host | Purpose | Sent |
|---|---|---|
| `data.ecmwf.int`, `ecmwf-forecasts.s3.eu-central-1.amazonaws.com`, `storage.googleapis.com` | ECMWF Open Data and its official mirrors | dataset paths, byte ranges |
| `eccharts.ecmwf.int`, `charts.ecmwf.int` | WMS layers, OpenCharts products | layer/product names, map area, time, point coordinates |
| `polytope.ecmwf.int` | Polytope extraction | request incl. coordinates; your ECMWF key (header) |
| `api.ecmwf.int` | ECMWF Web API / MARS | request; your ECMWF key (header) |
| `cds.climate.copernicus.eu`, `ads.atmosphere.copernicus.eu`, `ecds.ecmwf.int` | Copernicus and ECMWF data stores | dataset requests, licence check; your data-store token (header) |
| `polytope.lumi.apps.dte.destination-earth.eu`, `polytope.mn5.apps.dte.destination-earth.eu`, `polytope.leonardo.apps.dte.destination-earth.eu`, `auth.destine.eu`, `platform.destine.eu` | Destination Earth data and authentication | request; your DestinE token (header) |
| `aviationweather.gov` (NOAA Aviation Weather, the one non-ECMWF source) | ICAO station metadata; the latest METARs only when you ask for them — labelled non-ECMWF | station ids |
| `oscar.wmo.int` (WMO OSCAR/Surface) | station metadata for WMO ids missing from the bundled index | WMO station id |
| `apps.ecmwf.int`, `status.ecmwf.int` | ECMWF service status, only after a connection failure | nothing beyond the request |
| `pypi.org`, `files.pythonhosted.org` (via `uv`), `astral.sh` | Python packages; uv install instructions | package names |
| `unpkg.com` (in the generated web map page, loaded by your browser) | Leaflet map library | — |

Links shown to users only (not contacted by the scripts): `www.ecmwf.int`, `support.ecmwf.int`,
`creativecommons.org`, `doi.org`, `github.com`, `docs.astral.sh`, `codes.ecmwf.int`.

No personal data is collected. Credentials stay in the user's own files (`~/.ecmwfapirc`,
`~/.cdsapirc`, `~/.adsapirc`, `~/.polytopeapirc`, `~/.polytopeapirc-destine`); scripts check them
by name and never print them.

## Getting started and troubleshooting

No account is needed for Open Data, maps, charts or validating Copernicus requests. When a key,
licence or service is missing, the skills print a BLOCKED report with exact steps to resolve it.
Try: *"What's the weather in Bonn over the next 48 hours? Use ECMWF open data."*, *"Make a 3-day
meteogram for Reading."*, *"Prepare a validated ERA5 request for hourly 2 m temperature in
Lisbon, 1991–2020."*

Support: https://support.ecmwf.int · Privacy: https://www.ecmwf.int/en/privacy · Terms:
https://www.ecmwf.int/en/terms-use · Licence: Apache-2.0 (see `LICENSE`); the ECMWF logo is a
trademark used with permission. Data keeps its own licence (CC BY 4.0 for Open Data and most
Copernicus data; ECMWF licence for operational data).
