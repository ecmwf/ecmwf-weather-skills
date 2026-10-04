---
name: cds-ads
description: Finds, validates and downloads data from the Copernicus Climate Data Store (CDS) and Atmosphere Data Store (ADS), operated by ECMWF — ERA5 and ERA5-Land reanalysis (historical weather since 1940, hourly point time series, climate normals), seasonal forecasts, and CAMS air-quality and atmospheric-composition forecasts and reanalyses. Use when a task mentions ERA5, reanalysis, past weather, climate normals or trends, Copernicus, C3S, CAMS, CDS or ADS dataset ids or requests, cdsapi, ~/.cdsapirc, request size or queue limits, licence acceptance errors, or citing Copernicus data. Validates requests and costs them without an account; tells the user exactly how to get a key when one is missing.
compatibility: scripts/cds.py runs on plain Python 3 for check, search, describe, validate and era5-point --dry-run (public CDS/ADS APIs). Downloads use uv (PEP 723 inline dependency earthkit-data[cds]) and a CDS/ADS key. Network access to cds.climate.copernicus.eu / ads.atmosphere.copernicus.eu.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.4"
---

<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Copernicus CDS and ADS

## Contents
- Workflow (check → find → validate → retrieve)
- ERA5 at a point (history, climate normals)
- CAMS air quality without a key
- Processing the download
- Attribution and citation (required)
- References — `references/datasets.md` (common dataset ids, request examples, limits, errors)

Use ECMWF/Copernicus sources only — never substitute a third-party weather or climate API.

## Workflow

Run the scripts; don't read them. Run them from the user's working directory and write
files there, never inside the skill directory. Paths are relative to this skill.

```
- [ ] 1. python3 scripts/cds.py check                    # exit 4 = no key (names only, never values)
- [ ] 2. python3 scripts/cds.py search "TEXT" [--store ads] → dataset id
- [ ] 3. python3 scripts/cds.py describe ID               # allowed values, licence, DOI
- [ ] 4. write req.json; python3 scripts/cds.py validate --dataset ID --request req.json
         fix every error it lists (it suggests close matches), repeat until "valid"
- [ ] 5. key present → uv run scripts/cds.py retrieve --dataset ID --request req.json -o out.nc
         no key → give the validated request and offer setup instructions (below)
- [ ] 6. answer with the attribution and DOI the script printed
```

- Without a key, steps 1–4 still work: deliver a validated, costed request the user can run later.
  Always state the **dataset id** with the request JSON — the request is unusable without it.
- **Missing key:** say once what it would unlock (e.g. "a free CDS key would let me download
  this"), and **end the answer by asking** whether they'd like step-by-step instructions to get
  one — even when you also saved a validated request or a script. If yes, relay
  `python3 scripts/cds.py setup` (`--store ads` for CAMS) as printed. Don't dump the steps
  unasked.
- ADS keys: earthkit reads only `~/.adsapirc`; ADS's own page says `~/.cdsapirc` — `check`
  reports which one is present.
- `validate` checks keys and values locally and asks the server for cost vs limit; a request over
  the limit must be split (by year or month) or reduced (area, variables).
- Licences: each dataset's licence must be accepted once on its web page (Download tab) — the
  API refuses otherwise, and the script says so.
- Requests queue on the server; large ERA5 grids can take minutes to hours. Prefer the
  time-series and daily/monthly derived datasets when they answer the question.

## ERA5 at a point

```bash
python3 scripts/cds.py era5-point --lat 38.72 --lon -9.14 --start 1991-01-01 --end 2020-12-31 \
    --variable 2m_temperature,total_precipitation --dry-run          # validate + cost
uv run  scripts/cds.py era5-point --lat 38.72 --lon -9.14 --start 1991-01-01 --end 2020-12-31 \
    --variable 2m_temperature -o lisbon.csv                           # with a key
```

Uses `reanalysis-era5-single-levels-timeseries` (hourly, 1940 → ~5 days ago, CSV or NetCDF,
fast). Climate normals = 1991–2020 monthly means of that series (recipe in
`references/datasets.md`).

## CAMS air quality without a key

CAMS forecasts are also served keyless as WMS layers: the `opencharts-wms` skill's
`python3 scripts/wms.py info --layer composition_pm2p5 --lat LAT --lon LON --time ISO` returns
the value at a point (µg/m³); `wms.py layers --search pm10|ozone|dust|uv|pollen` lists others.
Use ADS (`--store ads`) for downloads, history or variables not in the WMS.

## Processing the download

NetCDF/GRIB: the `earthkit` skill (`earthkit-data` to read, `earthkit-transforms[all]` for
daily/monthly statistics and climatologies). CSV time series: pandas is fine.

## Attribution and citation

Required in anything published or displayed:

- `Generated using Copernicus Climate Change Service information <year>. Neither the European
  Commission nor ECMWF is responsible for any use that may be made of the Copernicus
  information or data it contains.` (ADS/CAMS: "Copernicus Atmosphere Monitoring Service";
  modified data: "Contains modified …").
- Cite the dataset DOI (`describe` prints it). Most ERA5/CAMS datasets are CC BY 4.0; read the
  licence per dataset from `describe`.
