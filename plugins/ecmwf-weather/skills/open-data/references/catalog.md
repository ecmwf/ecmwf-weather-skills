<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)

SPDX-License-Identifier: Apache-2.0
-->

# Open Data catalogue

Verify against the live server before relying on details — `python3 scripts/odcatalog.py fields`
lists exactly what a run contains. (The generated companion `fields.md` is refreshed weekly.)

## Roots

| `--source` | URL | Notes |
|---|---|---|
| `ecmwf` (default) | https://data.ecmwf.int/forecasts | ~2–3 days retained; 500 simultaneous connections |
| `aws` | https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com | archive starts 2023-01-18, anonymous |
| `google` | https://storage.googleapis.com/ecmwf-open-data | mirror |
| azure | https://ai4edataeuwest.blob.core.windows.net/ecmwf | needs a Planetary Computer SAS token; not supported by the stdlib script |

## Path

```
{root}/{YYYYMMDD}/{HH}z/{model}/0p25/{stream}/{YYYYMMDD}{HH}0000-{step}h-{stream}-{type}.{grib2|index}
```

Tropical-cyclone tracks: `…-{step}h-{stream}-tf.bufr`.

## Products (post IFS Cycle 50r1, 13 May 2026)

| Model | Runs | Stream-type | Steps |
|---|---|---|---|
| `ifs` | 00, 12 | `oper-fc`, `wave-fc`, `enfo-ef`, `waef-ef` | 0–144 by 3, 150–360 by 6 |
| `ifs` | 06, 18 | same | 0–144 by 3 |
| `ifs` | 00, 12 | `enfo-ep`, `waef-ep` (probabilities) | 240, 360 |
| `aifs-single` | 00, 06, 12, 18 | `oper-fc`, `wave-fc` | 0–360 by 6 |
| `aifs-ens` | 00, 06, 12, 18 | `enfo-cf`, `enfo-pf`, `waef-cf`, `waef-pf`, `enfo-ep` | 0–360 by 6 |

`ef` files hold the 50 IFS ENS perturbed members (select with `--number`). Before 50r1, 06/18z IFS
used streams `scda`/`scwv` to 90 h and ENS had a `cf` control member — the scripts handle the
naming for dates before the cutover.

## .index format

JSON lines, one per GRIB message:

```json
{"domain": "g", "date": "20261001", "time": "0000", "expver": "0001", "class": "od", "type": "fc",
 "stream": "oper", "step": "24", "levtype": "sfc", "param": "tp", "_offset": 0, "_length": 712183}
```

Optional keys: `levelist` (pressure level hPa / soil layer), `number` (ensemble member), `model`
(AIFS). AIFS uses `class: "ai"`. Download one field with
`Range: bytes={_offset}-{_offset+_length-1}`. A full IFS step file is ~150 MB; one surface field
is ~0.3–1 MB.

## Common parameters

| Short name | Meaning | Units | levtype |
|---|---|---|---|
| `2t` | 2 m temperature | K | sfc |
| `2d` | 2 m dewpoint | K | sfc |
| `mx2t3` / `mn2t3` | max / min 2 m temperature in last 3 h | K | sfc |
| `tp` | total precipitation, accumulated since step 0 | m | sfc |
| `tprate` | precipitation rate | kg m-2 s-1 | sfc |
| `ptype` | precipitation type (code) | — | sfc |
| `sf` | snowfall, accumulated | m w.e. | sfc |
| `sd` | snow depth | m w.e. | sfc |
| `10u` / `10v` | 10 m wind components | m/s | sfc |
| `100u` / `100v` | 100 m wind components | m/s | sfc |
| `10fg` | 10 m wind gust | m/s | sfc |
| `msl` | mean sea level pressure | Pa | sfc |
| `sp` | surface pressure | Pa | sfc |
| `tcc` | total cloud cover | 0–1 | sfc |
| `tcwv` | total column water vapour | kg m-2 | sfc |
| `ssrd` / `strd` | surface solar / thermal radiation downwards, accumulated | J m-2 | sfc |
| `mucape` | most-unstable CAPE | J kg-1 | sfc |
| `skt` | skin temperature | K | sfc |
| `lsm` | land–sea mask | 0–1 | sfc |
| `t`, `u`, `v`, `gh`, `r`, `q`, `w`, `vo`, `d` | upper air | various | pl (1000…10 hPa) |
| `vsw`, `sot` | soil moisture / temperature | | sol |
| `swh`, `mwp`, `mwd`, `pp1d` | waves (stream `wave`/`waef`) | m, s, deg | sfc |

Accumulated fields (`tp`, `sf`, `ssrd`, …) must be differenced between steps to get interval
totals; `odpoint.py` does this for `tp`.
