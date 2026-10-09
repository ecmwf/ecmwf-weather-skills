<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Observation codes

Full lists: report types https://codes.ecmwf.int/odb/reporttype/, variable numbers
https://codes.ecmwf.int/odb/varno/ (JSON: append `.json`).

## Report types (`reportype`, ODB feedback)

| Code | Reports | obs.py |
|---|---|---|
| 16076 | BUFR land SYNOP (most stations, often hourly) | SYNOP |
| 16001 / 16002 | automatic / manual land SYNOP (traditional) | SYNOP |
| 16004 / 16065 | METAR / automatic METAR (`statid` = ICAO id) | METAR |
| 16074, 16083, 16084 | ship SYNOP, moored and drifting buoys | — |

## Observation types (`obstype`, BUFR reports, `obsgroup=conv`)

`lsd` land surface data (all SYNOPs), `metar`, `ametar`; `ident` takes numeric WMO ids only.

## Variables (`varno`)

| varno | Variable | Units in ODB | obs.py |
|---|---|---|---|
| 39 | 2 m temperature | K | `t2m` °C |
| 40 | 2 m dew point | K | `td` °C |
| 58 | 2 m relative humidity | 0–1 | `rh` % |
| 41 / 42 | 10 m u / v wind | m/s | — |
| 111 / 112 | wind direction / speed | deg / m/s | `wind_dir`, `wind_speed` |
| 110 | pressure; `vertco_reference_1@body = 0` → mean sea level, else station pressure | Pa | `mslp` / `ps` hPa |
| 80 | precipitation, period in varno 79 (hours) | mm | `precip` + `period_h` |
| 37 / 38 | minimum / maximum 2 m temperature | K | — |
| 261 | maximum wind gust | m/s | — |

BUFR keys used: `airTemperature`, `dewpointTemperature`, `relativeHumidity`, `windSpeed`,
`windDirection`, `pressureReducedToMeanSeaLevel`, `nonCoordinatePressure`,
`altimeterSettingQnh` (METAR), `totalPrecipitationOrTotalWaterEquivalent` with `timePeriod`
(negative hours back), `totalPrecipitationPastNHours`. A negative amount (-0.1) is a trace.

## Missing values and QC

- Missing: ODB `-3.4e38`, BUFR `-1e100` (pdbufr returns NaN or None) — obs.py drops them.
- `datum_status@body` bits: 1 active (used by the analysis), 2 passive, 4 rejected,
  8 blacklisted. obs.py writes them as `qc` (e.g. `active`, `rejected+blacklisted`).
  Blacklisted values are not necessarily wrong — the variable may simply not be assimilated
  (e.g. precipitation).
- `fg_depar` = observation − first guess (short-range forecast at the observation),
  `an_depar` = observation − analysis; same units as the value.
- `statid@hdr` is an 8-character right-aligned string: `'   08535'`, `'    LPPT'`.
