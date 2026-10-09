<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# MARS observation requests

`obs.py series --dry-run` prints the requests it would send. Write requests by hand only for
what obs.py doesn't cover, and check them with the `ecmwf-mars` skill's `mars.py lint`
(observation types need no `levtype`/`param`; `filter` must be quoted).

## ODB feedback (default)

```
retrieve,
    class=od, type=ofb, stream=oper, expver=1,
    reportype=16001/16002/16076, format=odb,
    date=20261001/to/20261007, time=00/06/12/18,
    filter="select statid@hdr,date@hdr,time@hdr,lat@hdr,lon@hdr,stalt@hdr,varno@body,vertco_reference_1@body,obsvalue@body,fg_depar@body,an_depar@body,datum_status@body where statid@hdr = '   08535' and varno@body in (39,40,110,111,112)"
```

- Each `time` is a 6-hour window centred on it (00 = 21–03 UTC); the 00 window of the first
  day starts the evening before — obs.py trims to the requested days.
- The filter runs server-side: one station for a week returns a few KB. Every column needs its
  table (`@hdr`, `@body`). Several stations: `(statid@hdr = '   08535' or statid@hdr = '   08536')`;
  an area: `lat@hdr > 38.6 and lat@hdr < 38.9 and lon@hdr > -9.3 and lon@hdr < -9.0`.
- Measured: one station, 1 day (4 windows) ~30 s, 2 days ~2 min; obs.py sends one request per
  week.
- `type=mfb` is the same feedback as seen by the model (MARS restricts it like `ofb`).

## BUFR reports (`--raw`)

```
retrieve,
    class=od, type=ob, stream=oper, expver=1,
    obsgroup=conv, obstype=lsd,
    date=20261001/to/20261007, time=00/03/06/09/12/15/18/21, range=179,
    ident=08535
```

- `time` + `range=179` (minutes) tile each day in 3-hour slots.
- `ident` is a numeric WMO id. METARs have no numeric ident: `obstype=metar` with
  `area=N/W/S/E` around the airport (obs.py: ±0.1°), then filter by ICAO id when decoding.
- The server reads every report of each date (~0.4 GB a day) to filter: a week took 83 s for
  52 KB. Keep dates few; never loop day by day with separate requests.
- Some stations' hourly BUFR reports omit the temperature; the feedback is more complete.

## Latency and rights

| Data | In MARS after | Restricted |
|---|---|---|
| ODB feedback (`ofb`, `mfb`) | ~10 h after each window | while less than 24 h old |
| BUFR reports (`ob`) | ~2 days | always |

Accounts without these rights get a MARS error; obs.py turns it into a BLOCKED report with the
Computing Representative route.
