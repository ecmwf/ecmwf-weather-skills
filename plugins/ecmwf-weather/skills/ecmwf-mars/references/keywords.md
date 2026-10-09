<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)

SPDX-License-Identifier: Apache-2.0
-->

# MARS keywords and requests

## Contents
- Syntax
- Keywords
- Common values
- Example requests
- Errors

## Syntax

```
retrieve,
    class=od, stream=oper, type=fc, expver=1, levtype=sfc,
    param=2t/10u/10v, date=2024-03-01/to/2024-03-31, time=00,
    step=0/to/72/by/6, grid=0.25/0.25, area=72/-25/30/45,
    target="out.grib"
```

Lists `a/b/c`, ranges `a/to/b`, steps `a/to/b/by/n`. Dates `YYYY-MM-DD`, `YYYYMMDD` or relative
(`-1` = yesterday). Times `00`, `0000` or `00:00`. JSON form: same keys, values as strings.

## Keywords

| Keyword | Meaning | Required |
|---|---|---|
| `class` | archive/project: `od` operational, `ea` ERA5, `ai` AIFS, `rd` research | yes |
| `stream` | `oper` HRES, `enfo` ENS, `wave`, `waef`, `mmsf` seasonal, `moda` monthly means | yes |
| `type` | `an` analysis, `fc` forecast, `cf`/`pf` ENS control/perturbed, `em`/`es` ENS mean/spread, `ep` probabilities | yes |
| `levtype` | `sfc`, `pl` pressure, `ml` model levels, `pt`, `pv`, `sol` soil | yes |
| `levelist` | levels for pl/ml: `1000/850/500`, `1/to/137`, `all` | for pl/ml |
| `param` | short names (`2t`, `tp`, `t`, `u`) or ids (`167`, `167.128`) | yes |
| `date`, `time` | base date(s)/time(s) of the run or analysis | yes |
| `step` | forecast hours | forecasts |
| `number` | ENS members `1/to/50` | `pf` |
| `expver` | experiment version; `1` (=`0001`) for operational/final data | default 1 |
| `grid` | output grid `dlon/dlat` in degrees (interpolation) | no |
| `area` | `north/west/south/east` subdomain | no |
| `target` | output file (Web API scripts set it) | no |

## Common values

- Surface parameters: `2t` 167, `2d` 168, `tp` 228, `10u` 165, `10v` 166, `10fg` 49, `msl` 151,
  `sp` 134, `tcc` 164, `ssrd` 169, `cape` 59, `skt` 235.
- Radiation (accumulated J m-2 from the run start): `ssrd` 169, `ssr` 176, `strd` 175, `str` 177,
  `fdir` 228021 (direct solar radiation — not in Open Data; needed for UTCI, MRT, WBGT).
- Upper air: `t` 130, `u` 131, `v` 132, `z` 129 (geopotential, m² s⁻²), `q` 133, `r` 157, `w` 135.
- Operational HRES: 00/06/12/18 UTC runs; ENS: 50 perturbed members.
- ERA5: `class=ea, stream=oper, type=an, expver=1`, hourly `time=00/to/23/by/1`, 37 pressure
  levels, native N320 (~0.28°); ERA5 ensemble: `stream=enda`.

- CARRA/CERRA: `class=rr, stream=oper, expver=prod`, `type=an|fc`, `levtype=sfc|pl|hl|ml`,
  `origin` per product (`no-ar-cw`, `no-ar-ce`, `no-ar-pa`, `se-al-ec`, `fr-ms-ec`); 3-hourly
  analyses, forecasts to `step=30` from 00/12 UTC. Native Lambert grids (CARRA-West 1069×1269,
  CARRA-East 789×989, CERRA 1069×1069) — `area` only with `grid`.
- CERRA ensemble (CERRA-EDA): `origin=se-al-ec, stream=enda, type=an|fc, number=0/to/9` (always
  give `number`); 565×565 at 11 km, `time=00/06/12/18`, forecasts `step=1/to/6`.

## Example requests

IFS ENS 2 m temperature, all members, yesterday's 00 UTC run, 1° global:
```
retrieve, class=od, stream=enfo, type=pf, number=1/to/50, levtype=sfc, param=2t,
    date=-1, time=00, step=0/to/240/by/12, grid=1/1
```

ERA5 hourly temperature on pressure levels, one month (one tape-friendly chunk):
```
retrieve, class=ea, stream=oper, type=an, expver=1, levtype=pl, levelist=all, param=t,
    date=2010-01-01/to/2010-01-31, time=00/to/23/by/1, grid=0.25/0.25
```

## Errors

| Message | Fix |
|---|---|
| `User ... has no access to ...` / `not authorised` | account lacks MARS rights for that class/stream — use open-data, polytope or CDS |
| `No data` / `0 fields retrieved` | date/time/step not archived for that stream/type, or wrong `expver` |
| `Ambiguous value` / `Unknown value` | misspelt keyword value — check this table |
| `Request too large` / > 75 GB | split with `mars.py plan` and reduce grid/area |
| queued for a long time | normal for tape; don't resubmit, wait |
