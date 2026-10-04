---
name: destine
description: Handles any Destination Earth question — load it first; it prepares requests by running scripts/destine.py. Accesses Destination Earth (DestinE) Digital Twin data — the Climate Change Adaptation Digital Twin (Climate DT, multi-decadal IFS-FESOM, IFS-NEMO and ICON simulations and SSP3-7.0 projections), the Weather-Induced Extremes Digital Twin (Extremes DT, high-resolution forecasts of the last ~15 days) and On-Demand Extremes — through the DestinE Polytope service. Use when a task mentions Destination Earth, DestinE, Digital Twins, Climate DT, Extremes DT, nextGEMS, the DestinE Platform (DESP), desp-authentication, upgraded access, or polytope.lumi / polytope.mn5 addresses. Explains how to request the required upgraded access when the user has none; checks the token, builds and routes requests, and retrieves data.
compatibility: scripts/destine.py check, setup and request run on plain Python 3 offline. retrieve needs uv (PEP 723 inline dependency polytope-client), an approved DestinE upgraded-access account and network access to *.apps.dte.destination-earth.eu.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.4"
---

<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Destination Earth Digital Twins

## Contents
- Access check (always first)
- No access yet — offer the request instructions
- Request workflow
- Which server holds the data
- Licence and attribution (restricted)
- References — `references/requests.md` (keys and values, Climate DT and Extremes DT examples, feature extraction, limits, errors)

Use ECMWF and DestinE sources only — never substitute a third-party API. DestinE uses its own
platform account and token, separate from ECMWF API keys.

Run the scripts; don't read them. Run them from the user's working directory and write
files there, never inside the skill directory.

## Access check

```bash
python3 scripts/destine.py check --json     # never prints the token
```

Exit 0: a token exists in `~/.polytopeapirc-destine` (or `DESTINE_POLYTOPE_KEY`). Exit 4: none.

## No access yet

All Digital Twin data needs **upgraded access**, granted by the European Commission on request
(several business days). A basic DestinE Platform account is not enough. Say this once and
**end the answer by asking** whether they'd like step-by-step instructions; give them only if
the user accepts:

```bash
python3 scripts/destine.py setup      # register → request upgraded access → authenticate
```

Relay the steps as printed. Two rules: the token goes in `~/.polytopeapirc-destine` (the
official script's default, `~/.polytopeapirc`, would overwrite an ECMWF Polytope key), and the
password is typed at the prompt — never on the command line. Meanwhile, offer what works now:
the `open-data` skill for current forecasts, `cds-ads` for ERA5 climate data.

## Request workflow

Building a request needs no token or access — `request` works offline. Always run it, even
when `check` finds no token, and give its output.

```
- [ ] 1. python3 scripts/destine.py check
- [ ] 2. python3 scripts/destine.py request climate-dt|extremes-dt --param 167 [--model ifs-nemo]
         [--experiment SSP3-7.0 --activity projections] [--start YYYY-MM-DD --end YYYY-MM-DD]
         [--lat LAT --lon LON for a point time series] --json > req.json
- [ ] 3. Fix any "errors" it reports (keys and values: references/requests.md)
- [ ] 4. uv run scripts/destine.py retrieve req.json -o out.grib   (out.covjson for --lat/--lon)
- [ ] 5. Decode GRIB with the earthkit skill; give the attribution below
```

- If asked not to download, stop after step 3. **Run `request` yourself and paste its JSON**
  in the answer with the server address and one line on the licence terms (below) — not just
  the command.
- On HTTP 401 the token has expired: re-run the authentication step from `setup`. On 403 the
  account lacks upgraded access.
- Keep at most 2 downloads running at once.

## Which server holds the data

| Data | Server |
|---|---|
| Climate DT Generation 2 — IFS-FESOM, ICON | `polytope.lumi.apps.dte.destination-earth.eu` |
| Climate DT Generation 2 — IFS-NEMO; IFS-FESOM storylines (`story-nudging`) | `polytope.mn5.apps.dte.destination-earth.eu` |
| Extremes DT, On-Demand Extremes DT, nextGEMS | `polytope.lumi.apps.dte.destination-earth.eu` |
| Some Climate DT Generation 1 data | `polytope.leonardo.apps.dte.destination-earth.eu` |

`destine.py request` picks the server automatically; always state it with the request.

## Licence and attribution

Upgraded-access data is for official use under the European Commission's terms: no
redistribution of raw data without written consent; only derived products from which the
original data cannot be recovered may be shared. Credit, ending the answer:
"Based on data of the European Commission, using the Destination Earth Platform." For Climate DT
also cite: Destination Earth Digital Twin for Climate Change Adaptation (DestinE Climate DT
Generation 2), ECMWF, https://doi.org/10.21957/79c6af3105.
