<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)

SPDX-License-Identifier: Apache-2.0
-->

# Research — open questions, best guesses (2026-10-02)

Confidence: H/M/L. Everything below should be confirmed with ECMWF before v1.0.

## 1. Attribution

**Open Data (H)** — CC-BY-4.0 + ECMWF Terms of Use (https://apps.ecmwf.int/datasets/licences/general/).
For a *service built on the data* (our case), the required wording is:

```
This service is based on data and products of the European Centre for Medium-Range Weather Forecasts (ECMWF).
Source: www.ecmwf.int
This ECMWF data is published under a Creative Commons Attribution 4.0 International (CC BY 4.0). https://creativecommons.org/licenses/by/4.0/
ECMWF does not accept any liability whatsoever for any error or omission in the data, their availability, or for any loss or damage arising from their use.
```
For data products: first line becomes `© <year> European Centre for Medium-Range Weather Forecasts (ECMWF)`.
State modifications where applicable. **Best guess adopted:** short form in UIs
(`Data: © <year> ECMWF, CC BY 4.0`) linking to a full notice; full text in files/READMEs.

**CDS/ADS (H for CC-BY metadata, M for wording)** — ERA5, ERA5-Land, CAMS EAC4 and CAMS forecasts
now list `CC-BY-4.0` in the catalogue. ECMWF citation guidance still asks:
```
Generated using Copernicus Climate Change Service information <year>. Neither the European Commission nor ECMWF is responsible for any use that may be made of the Copernicus information or data it contains.
```
(CAMS: "Copernicus Atmosphere Monitoring Service"; modified data: "Contains modified …").
**Adopted:** always emit this sentence + dataset DOI; read licence id per dataset from the catalogue
(`https://cds.climate.copernicus.eu/api/catalogue/v1/collections/<id>`).

**MARS (M)** — governed by the user's licence. **Adopted:** `© <year> ECMWF` + disclaimer, and warn
that redistribution may be restricted.

**Charts / WMS (H OpenCharts, M WMS)** — OpenCharts responses carry `licence: CC-BY-4.0`. WMS has no
statement; assume same as Open Data. CAMS layers additionally get the CAMS sentence.

Alternatives: (a) only short credit everywhere; (b) full notice everywhere. Rejected — (a) under-
complies, (b) unusable on maps.

## 2. WMS / charts (H — tested live)

- Endpoint: `https://eccharts.ecmwf.int/wms/?token=public` (no token → 403). Personal key from
  https://api.ecmwf.int/v1/key/ unlocks non-public layers.
- 124 named layers: ~15 public met (`z500_public`, `t850_public`, `ws850_public`, `msl_public`,
  ENS mean/spread, cyclone strike probabilities), ~90 CAMS `composition_*`, plus `background`,
  `foreground`, `boundaries`, `grid`, `rivers`.
- **No public 2 m temperature or precipitation WMS layers** — affects the flagship demo (see PLAN §8).
- CRS EPSG:4326 (lat,lon axis order in 1.3.0), 3857, polar. `time` dimension (ISO8601 valid time).
  `GetLegend` per style.
- CORS reflected on GET. GetMap responds 302 → `/streaming/…png` (follow redirects).
- Errors are HTTP 200 `text/xml` ServiceExceptionReport — check content-type.
- Don't send `TIME` with static layers (`background`/`foreground`) — error.
- **OpenCharts API** (keyless, CORS): catalogue
  `https://charts.ecmwf.int/opencharts-api/v1/search/?package=opencharts` (~333 products incl. AIFS);
  product `…/v1/products/<name>/?base_time=…&step=…&projection=opencharts_europe&format=png` returns
  JSON with `data.link.href` to the PNG.

## 3. Polytope (H client, M policy)

- `polytope.ecmwf.int`, collection `ecmwf-mars` (operational od ~2 days + AIFS-single open-data
  section); DestinE servers with `destination-earth`.
- **No keyless access.** Member/co-operating-state NMHS users with ECMWF key; DestinE needs DESP.
- Features: `timeseries`, `verticalprofile`, `polygon`, `boundingbox`, `trajectory`, `circle`,
  `position`; output CoverageJSON.
- polytope-client 0.7.10: env `POLYTOPE_USER_KEY`, `POLYTOPE_USER_EMAIL`, `POLYTOPE_ADDRESS`
  (generally `POLYTOPE_<KEY>`); file `~/.polytopeapirc` JSON `{"user_email","user_key"}`; falls back
  to `~/.ecmwfapirc`; else interactive login.
- **Consequence:** for most users the point-forecast path is Open Data + earthkit-geo nearest point.
  Polytope is the "efficient" upgrade, not the default.

## 4. Icon (M)

- ECMWF logo is ECMWF IP, excluded from CC-BY; external use permitted only as a link to ecmwf.int.
- GitHub org avatar (`https://avatars.githubusercontent.com/u/6368067?s=400`) is the best ready asset.
- **Adopted:** neutral placeholder until ECMWF communications approves the logo
  (pressoffice@ecmwf.int). Since the repo is ECMWF-affiliated this is likely a formality.

## 5. Open Data state (H — live listings)

- IFS 50r1 (13 May 2026): `scda`/`scwv` discontinued; 06/18z use `oper`/`wave` to 144 h; ENS `cf`
  removed (50 `pf` members; control = HRES).
- Path: `{root}/YYYYMMDD/HHz/{ifs|aifs-single|aifs-ens}/0p25/{stream}/YYYYMMDDHH0000-{step}h-{stream}-{type}.grib2`
- IFS 00/12z oper: 0–144 by 3, 150–360 by 6. 06/18z: 0–144 by 3. AIFS: 0–360 by 6, all four runs.
- `.index` JSON-lines with `_offset`/`_length` → HTTP Range downloads of single fields.
- Retention ≈ 12–14 runs (2–3 days) on data.ecmwf.int; AWS mirror keeps history since 2023-01-18.
- Mirrors: `aws` (`ecmwf-forecasts.s3.eu-central-1.amazonaws.com`), `google`
  (`storage.googleapis.com/ecmwf-open-data`), `azure` (needs SAS token). Limit: 500 simultaneous
  connections on data.ecmwf.int.
- Docs lag the server — trust live listings and `.index` files.

## 6. Client env vars (H — source)

- cdsapi: `CDSAPI_URL`, `CDSAPI_KEY`, `CDSAPI_RC` (default `~/.cdsapirc`).
- ADS via earthkit: `~/.adsapirc` only.
- ecmwf-api-client: `ECMWF_API_URL` + `ECMWF_API_KEY` + `ECMWF_API_EMAIL` (all three), or
  `ECMWF_API_RC_FILE`, or `~/.ecmwfapirc` (JSON).
- polytope-client: see §3.
