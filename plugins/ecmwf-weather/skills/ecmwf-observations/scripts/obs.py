#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "ecmwf-api-client>=1.6",
#   "pyodc>=1.6",
#   "pdbufr>=0.15",
#   "earthkit-utils>=1.0",
#   "earthkit-geo>=1.1",
#   "scipy>=1.16",
#   "earthkit-meteo[scores]>=1.2",
#   "thermofeel>=2.3",
# ]
# ///
"""Station observations from ECMWF's MARS archive — series, thermal indices, forecast checks.

  stations QUERY        WMO id, ICAO id or name -> id, name, coordinates (bundled WMO OSCAR
                        index; ICAO ids through NOAA Aviation Weather station metadata)
  series                observations at stations from MARS: ODB feedback by default (the
                        observations ECMWF's analysis used, with first-guess and analysis
                        departures and QC flags, ~10 h behind real time) or BUFR reports
                        (--raw, ~2 days behind); --indices adds thermal-comfort indices
                        (thermofeel); --latest-metar fetches the latest METARs from NOAA
                        Aviation Weather instead — non-ECMWF, labelled, only on request
  verify                pair a point forecast (odpoint.py / ptpoint.py --json) with
                        observations by valid time: bias, MAE, RMSE per lead (earthkit-meteo)
  points                one variable at one time from a multi-station series, for
                        `ekplot.py map --obs`

  python3 obs.py stations 08535
  python3 obs.py series --station 08535 --days 7 --dry-run          # requests only
  uv run obs.py series --station 08535 --days 7 --vars t2m --csv -o lisbon.csv
  uv run obs.py series --station LPPT --start 2026-10-07 --end 2026-10-07 --indices
  uv run obs.py verify --station 08535 --forecast point.json -o pairs.csv
  python3 obs.py series --station EGLL --latest-metar               # NOAA, labelled

stations, --dry-run and --latest-metar are standard library. MARS retrievals need an ECMWF
Web API key (~/.ecmwfapirc) and an account with MARS observation rights.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import importlib
import io
import json
import math
import os
import re
import signal
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

# Never write __pycache__ into the installed skill (it may be read-only, and skills must
# not be modified); sibling scripts are imported below.
sys.dont_write_bytecode = True
ecmwf_status = importlib.import_module(
    "ecmwf_status"
)  # sibling script: what ECMWF says about service status

HERE = Path(__file__).resolve().parent
STATIONS_CSV = HERE.parent / "references" / "stations.csv"
UA = "ecmwf-weather-skills-observations/0.1"
TIMEOUT_S = 30

ECMWF_LABEL = "ECMWF MARS observations"
OSCAR_LABEL = "WMO OSCAR/Surface"
# The one non-ECMWF source (AGENTS.md: scoped exception): station metadata, and the latest
# METARs only when the user asks for them or has no MARS observation rights. Always labelled.
NOAA_LABEL = "NOAA Aviation Weather (non-ECMWF)"
NOAA_API = "https://aviationweather.gov/api/data"
OSCAR_SEARCH = "https://oscar.wmo.int/surface/rest/api/search/station"

ATTRIBUTION = {
    "ecmwf": "Observations from the WMO Global Observing System, retrieved from the ECMWF MARS "
    "archive (departures and QC flags: © ECMWF). Licensed ECMWF data — use under your "
    "organisation's ECMWF licence; credit '© ECMWF' and check before redistributing.",
    "noaa": "METAR reports from NOAA Aviation Weather Center (aviationweather.gov) — US "
    "government data, NOT from ECMWF.",
}

# --- variables ------------------------------------------------------------------------------------

# ODB varno codes (https://codes.ecmwf.int/odb/varno/). Surface pressure (110) appears twice per
# report: vertco_reference_1 = 0 (sea level) is the mean-sea-level pressure, the station's own
# geopotential the station pressure. Precipitation (80) carries its period in hours as 79.
VARNO = {"t2m": 39, "td": 40, "rh": 58, "wind_dir": 111, "wind_speed": 112, "mslp": 110}
VARNO["ps"] = VARNO["mslp"]
VARNO["precip"] = 80
PRECIP_PERIOD_VARNO = 79
UNITS = {
    "t2m": "degC",
    "td": "degC",
    "rh": "%",
    "wind_speed": "m/s",
    "wind_dir": "deg",
    "mslp": "hPa",
    "ps": "hPa",
    "qnh": "hPa",
    "precip": "mm",
    "raw": "",
}
GROUPS = {
    "t2m": ["t2m"],
    "td": ["td"],
    "rh": ["rh"],
    "wind": ["wind_speed", "wind_dir"],
    "mslp": ["mslp", "qnh"],
    "ps": ["ps"],
    "precip": ["precip"],
}
DEFAULT_VARS = "t2m,td,wind,mslp,precip"
INDICES = {  # thermofeel function, inputs, units
    "heat_index": ("calculate_heat_index_adjusted", ("t2m", "td"), "degC"),
    "humidex": ("calculate_humidex", ("t2m", "td"), "degC"),
    "apparent_temperature": ("calculate_apparent_temperature", ("t2m", "wind", "rh"), "degC"),
    "wind_chill": ("calculate_wind_chill", ("t2m", "wind"), "degC"),
    "discomfort_index": ("calculate_discomfort_index", ("t2m", "rh"), "degC"),
    "summer_simmer_index": ("calculate_summer_simmer_index", ("t2m", "rh"), "degC"),
    "relative_strain_index": ("calculate_relative_strain_index", ("t2m", "rh"), ""),
}
# Point-forecast JSON keys (odpoint.py / ptpoint.py) <-> observed variables.
POINT_KEYS = {
    "t2m_C": "t2m",
    "td_C": "td",
    "d2m_C": "td",
    "wind_speed_ms": "wind_speed",
    "msl_hPa": "mslp",
    **{f"{name}_C": name for name, spec in INDICES.items() if spec[2] == "degC"},
}

# --- MARS request shapes ----------------------------------------------------------------------

# ODB report types (https://codes.ecmwf.int/odb/reporttype/): land SYNOPs arrive as BUFR
# (16076) or traditional automatic/manual SYNOP (16001/16002); METARs as 16004 or 16065.
REPORTYPE = {"SYNOP": "16001/16002/16076", "METAR": "16004/16065"}
FEEDBACK_COLUMNS = (
    "statid@hdr,date@hdr,time@hdr,lat@hdr,lon@hdr,stalt@hdr,varno@body,"
    "vertco_reference_1@body,obsvalue@body,fg_depar@body,an_depar@body,datum_status@body"
)
FEEDBACK_TIMES = (0, 6, 12, 18)  # each a 6-hour assimilation window centred on the time
BUFR_TIMES = "00/03/06/09/12/15/18/21"  # with range=179 (minutes) these tile the whole day
# One feedback request scans ~0.5 GB per window server-side (filtered there); a week (28
# windows) returns in about 3 minutes. BUFR requests scan every report of a day (~0.4 GB).
CHUNK_DAYS = 7
# Latency measured on the archive: feedback ~10 h after its window ends, BUFR ~2 days.
FEEDBACK_LATENCY_H = 10
BUFR_LATENCY_DAYS = 2
METAR_BOX_DEG = 0.1  # BUFR METARs have no numeric ident: an area around the airport
MISSING_ABS = 1e30  # ODB uses -3.4e38, BUFR decoders -1e100 for missing values
QC_BITS = ((1, "active"), (2, "passive"), (4, "rejected"), (8, "blacklisted"))


# --- small helpers --------------------------------------------------------------------------------


def is_missing(v) -> bool:
    if v is None:
        return True
    try:
        f = float(v)
    except (TypeError, ValueError):
        return True
    return math.isnan(f) or abs(f) >= MISSING_ABS


def qc_label(status) -> str:
    """ODB datum_status bits: what the analysis did with the value."""
    if is_missing(status):
        return ""
    s = int(status)
    return "+".join(name for bit, name in QC_BITS if s & bit)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def parse_iso(s: str) -> datetime:
    s = s.strip().replace("Z", "")
    for fmt in ("%Y-%m-%dT%H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        with contextlib.suppress(ValueError):
            return datetime.strptime(s, fmt).replace(tzinfo=UTC)
    raise ValueError(f"not a time: {s!r} (use e.g. 2026-10-09T12:00Z)")


def parse_day(s: str, today: date | None = None) -> date:
    today = today or datetime.now(UTC).date()
    if re.fullmatch(r"-\d+", s):
        return today + timedelta(days=int(s))
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        with contextlib.suppress(ValueError):
            return datetime.strptime(s, fmt).date()
    raise ValueError(f"not a date: {s!r} (use YYYY-MM-DD, or -N for N days ago)")


def classify(station: str) -> tuple[str, str]:
    s = station.strip()
    if re.fullmatch(r"\d{4,5}", s):
        return "wmo", s.zfill(5)
    # four characters, letter first: an ICAO id ("LPPT", "lppt", "K1G4"), unless written as
    # a capitalised word ("Oslo"), which is a name
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9]{3}", s) and not (s[0].isupper() and s[1:].islower()):
        return "icao", s.upper()
    return "name", s


def statid(ident: str) -> str:
    """ODB station ids are 8-character strings, right-aligned."""
    return ident.rjust(8)


def parse_vars(spec: str | None) -> list[str]:
    out: list[str] = []
    for item in (spec or DEFAULT_VARS).split(","):
        item = item.strip().lower()
        if item not in GROUPS:
            raise ValueError(f"unknown variable {item!r}; choose from {', '.join(GROUPS)}")
        out += [v for v in GROUPS[item] if v not in out]
    return out


# --- station metadata -----------------------------------------------------------------------------


class NoaaUnavailable(OSError):
    """NOAA Aviation Weather could not be reached."""


def _get_json(url: str, headers: dict | None = None):
    """GET JSON; None for an empty answer (HTTP 204)."""
    req = urllib.request.Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        body = r.read()
    return json.loads(body) if body.strip() else None


def load_stations(path: Path = STATIONS_CSV) -> list[dict]:
    """The bundled index of WMO land stations (generated from WMO OSCAR/Surface)."""
    with open(path, newline="", encoding="utf-8") as fh:
        rows = csv.DictReader(line for line in fh if not line.startswith("#"))
        return [
            {
                "wmo": r["wmo"],
                "icao": None,
                "name": r["name"],
                "lat": float(r["lat"]),
                "lon": float(r["lon"]),
                "elevation": float(r["elevation"]) if r["elevation"] else None,
                "territory": r["territory"],
                "source": f"{OSCAR_LABEL} (bundled index)",
            }
            for r in rows
        ]


def search_stations(query: str, stations: list[dict], limit: int = 20) -> list[dict]:
    kind, ident = classify(query)
    if kind == "wmo":
        return [s for s in stations if s["wmo"] == ident][:limit]
    q = query.strip().lower()
    hits = [s for s in stations if q in s["name"].lower() or q == s["territory"].lower()]
    return sorted(hits, key=lambda s: (not s["name"].lower().startswith(q), s["name"]))[:limit]


def noaa_station_info(icao: str, fetch=_get_json) -> dict | None:
    """ICAO -> WMO id and coordinates (NOAA Aviation Weather station metadata, labelled)."""
    url = f"{NOAA_API}/stationinfo?ids={urllib.parse.quote(icao)}&format=json"
    try:
        body = fetch(url, {"User-Agent": UA, "Accept": "application/json"})
    except (OSError, ValueError) as e:
        raise NoaaUnavailable(str(e)) from e
    for s in body or []:
        if str(s.get("icaoId", "")).upper() == icao.upper():
            return {
                "wmo": s.get("wmoId") or None,
                "icao": s["icaoId"],
                "name": s.get("site", ""),
                "lat": s.get("lat"),
                "lon": s.get("lon"),
                "elevation": s.get("elev"),
                "territory": s.get("country", ""),
                "source": f"{NOAA_LABEL} station metadata",
            }
    return None


def oscar_station(wmo: str, fetch=_get_json) -> dict | None:
    """Fallback registry for WMO ids missing from the bundled index (metadata only)."""
    url = f"{OSCAR_SEARCH}?wigosId=0-20000-0-{wmo}"
    try:
        body = fetch(url, {"User-Agent": UA, "Accept": "application/json"})
    except (OSError, ValueError):
        return None
    for s in (body or {}).get("stationSearchResults", []):
        return {
            "wmo": wmo,
            "icao": None,
            "name": s.get("name", ""),
            "lat": s.get("latitude"),
            "lon": s.get("longitude"),
            "elevation": s.get("elevation"),
            "territory": s.get("territory", ""),
            "source": f"{OSCAR_LABEL} (live)",
        }
    return None


def resolve_station(query: str, stations: list[dict] | None = None, fetch=_get_json) -> dict:
    """WMO id, ICAO id or a unique name -> station dict. LookupError when unknown or ambiguous."""
    stations = load_stations() if stations is None else stations
    kind, ident = classify(query)
    if kind == "wmo":
        hits = search_stations(ident, stations)
        if hits:
            return hits[0]
        found = oscar_station(ident, fetch)
        # SYNOP requests need only the id; coordinates are optional.
        return found or {"wmo": ident, "icao": None, "name": "", "lat": None, "lon": None}
    if kind == "icao":
        found = noaa_station_info(ident, fetch)
        if found is None:
            raise LookupError(f"no station with ICAO id {ident} (NOAA Aviation Weather)")
        if found["wmo"]:
            idx = search_stations(found["wmo"], stations)
            if idx:  # keep ICAO and NOAA's airport name, the WMO record's coordinates
                found = {**idx[0], "icao": found["icao"], "source": found["source"]}
        return found
    hits = search_stations(ident, stations)
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise LookupError(f"no station matches {query!r}; try a WMO id or an ICAO id")
    names = "; ".join(f"{h['wmo']} {h['name']} ({h['territory']})" for h in hits[:10])
    raise LookupError(f"{query!r} matches {len(hits)} stations: {names} — pass a WMO id")


# --- MARS requests --------------------------------------------------------------------------------


def report_kind(st: dict, prefer: str = "auto") -> str:
    if prefer == "metar" or (prefer == "auto" and not st.get("wmo")):
        if not st.get("icao"):
            raise ValueError(f"METARs need an ICAO id; station {st.get('wmo')} has none")
        return "METAR"
    if not st.get("wmo"):
        raise ValueError(f"SYNOP reports need a WMO id; {st.get('icao')} has none")
    return "SYNOP"


def odb_filter(idents: list[str], variables: list[str]) -> str:
    varnos = sorted({VARNO[v] for v in variables if v in VARNO})
    if "precip" in variables:
        varnos = sorted({*varnos, PRECIP_PERIOD_VARNO})
    who = " or ".join(f"statid@hdr = '{statid(i)}'" for i in idents)
    if len(idents) > 1:
        who = f"({who})"
    return (
        f'"select {FEEDBACK_COLUMNS} where {who} and varno@body in ({",".join(map(str, varnos))})"'
    )


def _date_range(d0: date, d1: date) -> str:
    return d0.strftime("%Y%m%d") if d0 == d1 else f"{d0:%Y%m%d}/to/{d1:%Y%m%d}"


def _chunks(start: date, end: date, days: int = CHUNK_DAYS) -> list[tuple[date, date]]:
    out, d = [], start
    while d <= end:
        e = min(end, d + timedelta(days=days - 1))
        out.append((d, e))
        d = e + timedelta(days=1)
    return out


def build_requests(
    stations: dict | list[dict],
    start: date,
    end: date,
    variables: list[str],
    raw: bool = False,
    prefer: str = "auto",
) -> list[dict]:
    """MARS requests (keyword dicts) for the stations and days, chunked by week. Windows not
    archived yet are requested too: MARS returns what exists without failing."""
    stations = [stations] if isinstance(stations, dict) else stations
    by_kind: dict[str, list[dict]] = {}
    for st in stations:
        by_kind.setdefault(report_kind(st, prefer), []).append(st)
    base = {"class": "od", "stream": "oper", "expver": "1"}
    reqs = []
    for kind, sts in by_kind.items():
        if raw:
            for d0, d1 in _chunks(start, end):
                common = {
                    **base,
                    "type": "ob",
                    "obsgroup": "conv",
                    "date": _date_range(d0, d1),
                    "time": BUFR_TIMES,
                    "range": "179",
                }
                if kind == "SYNOP":
                    reqs.append(
                        {**common, "obstype": "lsd", "ident": "/".join(s["wmo"] for s in sts)}
                    )
                    continue
                for s in sts:
                    if s.get("lat") is None:
                        raise ValueError(f"no coordinates for {s['icao']}: cannot box METARs")
                    b = METAR_BOX_DEG
                    area = f"{s['lat'] + b:g}/{s['lon'] - b:g}/{s['lat'] - b:g}/{s['lon'] + b:g}"
                    reqs.append({**common, "obstype": "metar", "area": area})
            continue
        idents = [s["wmo"] if kind == "SYNOP" else s["icao"] for s in sts]
        common = {**base, "type": "ofb", "reportype": REPORTYPE[kind], "format": "odb"}
        times = "/".join(f"{t:02d}" for t in FEEDBACK_TIMES)
        flt = odb_filter(idents, variables)
        for d0, d1 in _chunks(start, end):
            reqs.append({**common, "date": _date_range(d0, d1), "time": times, "filter": flt})
    return reqs


def mars_text(req: dict) -> str:
    return "retrieve,\n" + ",\n".join(f"    {k}={v}" for k, v in req.items())


def lint_request(req: dict) -> list[str]:
    """The observation-request rules of the ecmwf-mars skill's `mars.py lint`."""
    errs = []
    for k in ("class", "type", "stream", "expver", "date", "time"):
        if k not in req:
            errs.append(f"missing '{k}'")
    dates = str(req.get("date", "")).split("/")
    if len(dates) == 2:
        errs.append(f"date={req['date']} means just those two days — use a/to/b")
    if req.get("type") == "ofb":
        if req.get("format") != "odb":
            errs.append("type=ofb (ODB feedback) needs format=odb")
        if "reportype" not in req and "obsgroup" not in req:
            errs.append("type=ofb needs reportype (e.g. 16076 land SYNOP)")
        f = str(req.get("filter", ""))
        if f and not (f.startswith('"') and f.endswith('"')):
            errs.append("filter must be double-quoted")
        cols = re.match(r'"select (.*?)(?: where |")', f)
        if cols and any("@" not in c for c in cols.group(1).split(",")):
            errs.append("filter columns need their table: statid@hdr, obsvalue@body")
    elif req.get("type") == "ob":
        if "obstype" not in req and "obsgroup" not in req:
            errs.append("type=ob needs obstype (e.g. lsd land surface, metar)")
        ident = str(req.get("ident", ""))
        if ident and not all(p.isdigit() for p in ident.split("/")):
            errs.append("ident is numeric (WMO block+station); for METARs use area")
    else:
        errs.append(f"type={req.get('type')} is not an observation type (ob, ofb, mfb)")
    if "area" in req:
        a = [float(x) for x in str(req["area"]).split("/")]
        if len(a) != 4 or a[0] < a[2]:
            errs.append("area must be north/west/south/east")
    return errs


def latency_note(end: date, raw: bool, now: datetime | None = None) -> str | None:
    now = now or datetime.now(UTC)
    if raw:
        if end > now.date() - timedelta(days=BUFR_LATENCY_DAYS):
            return (
                f"BUFR reports reach MARS about {BUFR_LATENCY_DAYS} days after observation "
                f"time: reports after {now.date() - timedelta(days=BUFR_LATENCY_DAYS + 1)} may "
                "be missing — drop --raw for the feedback (~10 h behind)."
            )
        return None
    last = datetime(end.year, end.month, end.day, 21, tzinfo=UTC)  # end of the 18 UTC window
    if last + timedelta(hours=FEEDBACK_LATENCY_H) > now:
        return (
            f"ODB feedback reaches MARS about {FEEDBACK_LATENCY_H} h after each 6-hour "
            "window: the most recent hours are not archived yet."
        )
    return None


# --- decoding (pyodc / pdbufr; imported inside) ---------------------------------------------------


def _convert(rows: list[dict], src_units: dict) -> None:
    """Convert values (and departures) to output units in place, with earthkit-utils."""
    import numpy as np
    from earthkit.utils.units import convert_units

    target = {"K": "degC", "Pa": "hPa", "dimensionless": "percent"}
    for var, src in src_units.items():
        sel = [r for r in rows if r["variable"] == var]
        if not sel or src not in target:
            continue
        vals = convert_units(np.array([r["value"] for r in sel], float), target[src], src)
        for r, v in zip(sel, vals, strict=True):
            r["value"] = round(float(v), 2)
        if src == "K":
            continue  # a temperature difference in K is the same in °C
        for key in ("fg_depar", "an_depar"):
            idx = [i for i, r in enumerate(sel) if r[key] is not None]
            if idx:
                scale = "hPa" if src == "Pa" else "percent"
                d = convert_units(np.array([sel[i][key] for i in idx], float), scale, src)
                for i, v in zip(idx, d, strict=True):
                    sel[i][key] = round(float(v), 2)


def _dedupe(rows: list[dict]) -> list[dict]:
    seen, out = set(), []
    for r in rows:
        key = (r["station"], r["time_utc"], r["variable"], r.get("period_h"))
        if key not in seen:
            seen.add(key)
            out.append(r)
    return sorted(out, key=lambda r: (r["station"], r["time_utc"], r["variable"]))


def _row(station, time_utc, variable, value, report, **extra) -> dict:
    return {
        "station": station,
        "time_utc": time_utc,
        "variable": variable,
        "value": value,
        "units": extra.get("units", UNITS.get(variable, "")),
        "period_h": extra.get("period_h"),
        "fg_depar": extra.get("fg_depar"),
        "an_depar": extra.get("an_depar"),
        "qc": extra.get("qc", ""),
        "report": report,
        "source": extra.get("source", ECMWF_LABEL),
    }


def decode_feedback(path, stations=None) -> list[dict]:
    """Tidy rows from ODB feedback: value, first-guess/analysis departures, QC flag."""
    import pyodc

    df = pyodc.read_odb(str(path), single=True) if os.path.getsize(path) else None
    if df is None or df.empty:
        return []
    recs = df.to_dict("records")
    periods = {
        (r["statid@hdr"], r["date@hdr"], r["time@hdr"]): r["obsvalue@body"]
        for r in recs
        if r["varno@body"] == PRECIP_PERIOD_VARNO and not is_missing(r["obsvalue@body"])
    }
    by_varno = {VARNO[v]: v for v in ("t2m", "td", "rh", "wind_dir", "wind_speed", "precip")}
    rows = []
    for r in recs:
        varno, value = int(r["varno@body"]), r["obsvalue@body"]
        if is_missing(value) or varno == PRECIP_PERIOD_VARNO:
            continue
        if varno == VARNO["mslp"]:
            var = "mslp" if r.get("vertco_reference_1@body") == 0 else "ps"
        elif varno in by_varno:
            var = by_varno[varno]
        else:
            continue
        sid = str(r["statid@hdr"]).strip()
        d, t = int(r["date@hdr"]), int(r["time@hdr"])
        when = datetime.strptime(f"{d:08d}{t:06d}", "%Y%m%d%H%M%S").replace(tzinfo=UTC)
        extra = {
            "fg_depar": None if is_missing(r.get("fg_depar@body")) else r["fg_depar@body"],
            "an_depar": None if is_missing(r.get("an_depar@body")) else r["an_depar@body"],
            "qc": qc_label(r.get("datum_status@body")),
        }
        if var == "precip":
            p = periods.get((r["statid@hdr"], d, t))
            extra["period_h"] = None if p is None else int(abs(p))
        report = "SYNOP" if sid.isdigit() else "METAR"
        rows.append(_row(sid, iso(when), var, float(value), report, **extra))
    _convert(rows, {"t2m": "K", "td": "K", "rh": "dimensionless", "mslp": "Pa", "ps": "Pa"})
    for r in rows:
        r["value"] = round(r["value"], 2)
        for k in ("fg_depar", "an_depar"):
            if r[k] is not None:
                r[k] = round(float(r[k]), 2)
    return _dedupe(rows)


BUFR_STATE = {  # BUFR key -> (variable, source units)
    "airTemperature": ("t2m", "K"),
    "dewpointTemperature": ("td", "K"),
    "relativeHumidity": ("rh", None),
    "windSpeed": ("wind_speed", None),
    "windDirection": ("wind_dir", None),
    "pressureReducedToMeanSeaLevel": ("mslp", "Pa"),
    "nonCoordinatePressure": ("ps", "Pa"),
    "altimeterSettingQnh": ("qnh", "Pa"),
}
BUFR_PRECIP_FIXED = {f"totalPrecipitationPast{h}Hours": h for h in (1, 3, 6, 12, 24)}


def decode_bufr(path, stations) -> list[dict]:
    """Tidy rows from BUFR reports (SYNOP by WMO id, METAR by ICAO id), missing values masked."""
    import pdbufr

    stations = [stations] if isinstance(stations, dict) else stations
    if not os.path.getsize(path):
        return []
    rows: list[dict] = []
    wmo = [int(s["wmo"]) for s in stations if s.get("wmo") and not s.get("_metar")]
    icao = [s["icao"] for s in stations if s.get("icao") and (s.get("_metar") or not s["wmo"])]
    for idkey, ids, report in (
        ("WMO_station_id", wmo, "SYNOP"),
        ("icaoLocationIndicator", icao, "METAR"),
    ):
        if not ids:
            continue
        flt = {idkey: ids if len(ids) > 1 else ids[0]}
        req = [idkey, "data_datetime"]
        df = pdbufr.read_bufr(
            str(path), columns=[*req, *BUFR_STATE], filters=flt, required_columns=req
        )
        for r in df.to_dict("records"):
            sid = f"{int(r[idkey]):05d}" if report == "SYNOP" else str(r[idkey]).strip()
            t = iso(r["data_datetime"].to_pydatetime().replace(tzinfo=UTC))
            for key, (var, _) in BUFR_STATE.items():
                if key in r and not is_missing(r[key]):
                    rows.append(_row(sid, t, var, float(r[key]), report))
        precip = [
            ([*req, "timePeriod", "totalPrecipitationOrTotalWaterEquivalent"], None),
            *(([*req, k], h) for k, h in BUFR_PRECIP_FIXED.items()),
        ]
        for cols, fixed in precip:
            df = pdbufr.read_bufr(str(path), columns=cols, filters=flt, required_columns=cols)
            for r in df.to_dict("records"):
                v = r[cols[-1]]
                period = fixed or (None if is_missing(r.get("timePeriod")) else r["timePeriod"])
                if is_missing(v) or period is None:
                    continue
                sid = f"{int(r[idkey]):05d}" if report == "SYNOP" else str(r[idkey]).strip()
                t = iso(r["data_datetime"].to_pydatetime().replace(tzinfo=UTC))
                # BUFR codes a trace of precipitation as -0.1 kg m-2
                qc = "trace" if v < 0 else ""
                value, hours = max(float(v), 0.0), int(abs(period))
                rows.append(_row(sid, t, "precip", value, report, period_h=hours, qc=qc))
    _convert(rows, {var: u for var, u in BUFR_STATE.values() if u})
    for r in rows:
        r["value"] = round(r["value"], 2)
    return _dedupe(rows)


# --- NOAA Aviation Weather: latest METARs (non-ECMWF, labelled) ---------------------------------

KNOT_MS = 1852 / 3600  # exact definition; the stdlib route has no unit library


def noaa_latest_metar(icao: str, hours: int = 3, fetch=_get_json) -> list[dict]:
    """The latest METARs from NOAA Aviation Weather — every row labelled non-ECMWF."""
    url = f"{NOAA_API}/metar?ids={urllib.parse.quote(icao)}&format=json&hours={int(hours)}"
    try:
        body = fetch(url, {"User-Agent": UA, "Accept": "application/json"})
    except (OSError, ValueError) as e:
        raise NoaaUnavailable(str(e)) from e
    rows = []
    for m in body or []:
        t = iso(datetime.fromtimestamp(int(m["obsTime"]), UTC))
        sid = m.get("icaoId", icao)

        def add(var, value, sid=sid, t=t):
            if value is not None and value != "" and (var == "raw" or not is_missing(value)):
                rows.append(_row(sid, t, var, value, "METAR", source=NOAA_LABEL))

        add("t2m", m.get("temp"))
        add("td", m.get("dewp"))
        if isinstance(m.get("wspd"), int | float):
            add("wind_speed", round(m["wspd"] * KNOT_MS, 2))
        if isinstance(m.get("wdir"), int | float):  # "VRB" = variable direction
            add("wind_dir", m["wdir"])
        add("qnh", m.get("altim"))
        add("mslp", m.get("slp"))
        add("raw", m.get("rawOb"))
    return sorted(rows, key=lambda r: (r["time_utc"], r["variable"]))


# --- thermal indices, summaries, local time -------------------------------------------------------


def _by_time(rows: list[dict]) -> dict[tuple[str, str], dict]:
    out: dict[tuple[str, str], dict] = {}
    for r in rows:
        if r["variable"] in ("t2m", "td", "rh", "wind_speed"):
            out.setdefault((r["station"], r["time_utc"]), {"_row": r})[r["variable"]] = r["value"]
    return out


def compute_indices(rows: list[dict]) -> tuple[list[dict], list[str]]:
    """Thermal-comfort indices from temperature, humidity and wind (thermofeel); radiation-based
    ones (UTCI, WBGT, MRT) need radiation that stations don't report. RH and dew point are
    derived from each other with earthkit-meteo when only one is observed."""
    import numpy as np
    import thermofeel
    from earthkit.meteo import thermo
    from earthkit.utils.units import convert_units

    groups = _by_time(rows)
    keys = sorted(groups)
    notes = []

    def col(var):
        return np.array([groups[k].get(var, np.nan) for k in keys], float)

    t_c, td_c, rh, va = col("t2m"), col("td"), col("rh"), col("wind_speed")
    t_k = convert_units(t_c, "K", "degC")
    td_k = convert_units(td_c, "K", "degC")
    rh_from_td = thermo.relative_humidity_from_dewpoint(t_k, td_k)
    rh = np.where(np.isnan(rh), rh_from_td, rh)
    td_k = np.where(np.isnan(td_k), thermo.dewpoint_from_relative_humidity(t_k, rh), td_k)
    inputs = {"t2m": t_k, "td": td_k, "rh": rh, "wind": va}
    out = []
    version = getattr(thermofeel, "__version__", "")
    for name, (fn, needs, units) in INDICES.items():
        with np.errstate(all="ignore"):
            vals = getattr(thermofeel, fn)(*(inputs[n] for n in needs))
        if units == "degC":
            vals = convert_units(vals, "degC", "K")
        complete = ~np.any([np.isnan(inputs[n]) for n in needs], axis=0)
        for k, v, ok in zip(keys, vals, complete, strict=True):
            if ok and np.isfinite(v):
                src = groups[k]["_row"]
                value = round(float(v), 2)
                source = f"thermofeel {version} from {src['source']}"
                out.append(_row(*k, name, value, src["report"], units=units, source=source))
    for (sid, t), g in sorted(groups.items()):
        if "t2m" not in g:
            notes.append(f"{sid} {t}: no 2 m temperature — no indices")
            continue
        if "td" not in g and "rh" not in g:
            notes.append(f"{sid} {t}: no humidity — only wind chill")
        if "wind_speed" not in g:
            notes.append(f"{sid} {t}: no wind — no apparent temperature or wind chill")
    notes.append(
        "Each index is meaningful only in its own range (wind chill: cold and windy; heat "
        "index, humidex: warm and humid); radiation-based indices (UTCI, WBGT) need "
        "radiation that stations do not report."
    )
    return out, notes


def summarise(rows: list[dict]) -> dict:
    """Per variable: count, min and max with their times, mean; precipitation per period with
    the total of reports that tile time without overlap (hour divisible by the period)."""
    out: dict = {}
    groups: dict[str, list[dict]] = {}
    for r in rows:
        if isinstance(r["value"], int | float) and r["variable"] != "wind_dir":
            key = r["variable"]
            if key == "precip":
                key = f"precip_{r['period_h']}h"
            groups.setdefault(key, []).append(r)
    for key, rs in sorted(groups.items()):
        lo = min(rs, key=lambda r: r["value"])
        hi = max(rs, key=lambda r: r["value"])
        s = {
            "n": len(rs),
            "units": rs[0]["units"],
            "min": {"value": lo["value"], "time_utc": lo["time_utc"], "station": lo["station"]},
            "max": {"value": hi["value"], "time_utc": hi["time_utc"], "station": hi["station"]},
        }
        if key.startswith("precip_"):
            p = rs[0]["period_h"]
            tiling = [r for r in rs if int(r["time_utc"][11:13]) % p == 0]
            s["total"] = round(sum(r["value"] for r in tiling), 2)
            s["total_reports"] = len(tiling)
        else:
            s["mean"] = round(sum(r["value"] for r in rs) / len(rs), 2)
        out[key] = s
    return out


def add_local_time(rows: list[dict], tz: str) -> None:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(tz)
    for r in rows:
        r["local_time"] = parse_iso(r["time_utc"]).astimezone(zone).isoformat(timespec="minutes")


# --- verification ---------------------------------------------------------------------------------


def _obs_index(rows: list[dict]) -> dict[str, list[tuple[datetime, float]]]:
    idx: dict[str, list[tuple[datetime, float]]] = {}
    for r in rows:
        if isinstance(r["value"], int | float) and r["variable"] != "precip":
            idx.setdefault(r["variable"], []).append((parse_iso(r["time_utc"]), r["value"]))
    return idx


def pair(forecast: dict, rows: list[dict], tolerance_min: int = 30) -> list[dict]:
    """Forecast/observation pairs by valid time (nearest report within the tolerance)."""
    idx = _obs_index(rows)
    tol = timedelta(minutes=tolerance_min)
    pairs = []
    for f in forecast.get("series", []):
        vt = parse_iso(f["valid_time"])
        for key, var in POINT_KEYS.items():
            if f.get(key) is None or var not in idx:
                continue
            best = min(idx[var], key=lambda o: abs(o[0] - vt))
            if abs(best[0] - vt) <= tol:
                pairs.append(
                    {
                        "valid_time": f["valid_time"],
                        "step": f.get("step"),
                        "variable": var,
                        "forecast": f[key],
                        "observed": best[1],
                        "error": round(f[key] - best[1], 2),
                        "obs_time_utc": iso(best[0]),
                    }
                )
    return pairs


def scores(pairs: list[dict]) -> dict:
    """Bias (forecast minus observed), MAE and RMSE overall and per lead, with earthkit-meteo."""
    import xarray as xr
    from earthkit.meteo import score

    def stats(ps):
        f = xr.DataArray([p["forecast"] for p in ps], dims=["pair"])
        o = xr.DataArray([p["observed"] for p in ps], dims=["pair"])
        return {
            "n": len(ps),
            "bias": round(float(score.mean_error(f, o, over="pair")), 3),
            "mae": round(float(score.mean_abs_error(f, o, over="pair")), 3),
            "rmse": round(float(score.root_mean_squared_error(f, o, over="pair")), 3),
        }

    out = {}
    for var in sorted({p["variable"] for p in pairs}):
        ps = [p for p in pairs if p["variable"] == var]
        steps = sorted({p["step"] for p in ps}, key=lambda s: (s is None, s))
        out[var] = {
            "overall": stats(ps),
            "by_step": {str(s): stats([p for p in ps if p["step"] == s]) for s in steps},
        }
    return out


def gridpoint_distance_km(forecast: dict, station: dict) -> float | None:
    """Distance between the forecast's gridpoint and the station (earthkit-geo)."""
    from earthkit.geo import distance

    gp = forecast.get("gridpoint") or forecast.get("location") or {}
    if None in (gp.get("lat"), gp.get("lon"), station.get("lat"), station.get("lon")):
        return None
    d = distance.haversine_distance((gp["lat"], gp["lon"]), (station["lat"], station["lon"]))
    return round(float(d) / 1000.0, 1)


def with_observations(forecast: dict, rows: list[dict], station: dict | None = None) -> dict:
    """The point JSON plus an `observations` block that `ekplot.py meteogram` overlays."""
    back = {}
    for key, var in POINT_KEYS.items():
        back.setdefault(var, key)
    series: dict[str, dict] = {}
    for r in rows:
        if r["variable"] in back and isinstance(r["value"], int | float):
            series.setdefault(r["time_utc"], {"valid_time": r["time_utc"]})[back[r["variable"]]] = (
                r["value"]
            )
    sources = sorted({r["source"] for r in rows})
    attribution = dict(forecast.get("attribution") or {})
    credit = "; ".join("© ECMWF (licensed)" if s == ECMWF_LABEL else s for s in sources)
    if credit:
        short = attribution.get("short") or "Forecast: © ECMWF"
        attribution["short"] = f"{short}; observations: {credit}"
    return {
        **forecast,
        "attribution": attribution,
        "observations": {
            "source": ", ".join(sources) or ECMWF_LABEL,
            "label": f"observed ({', '.join(sources) or ECMWF_LABEL})",
            "station": station or {},
            "series": [series[t] for t in sorted(series)],
        },
    }


# --- output ---------------------------------------------------------------------------------------

CSV_FIELDS = [
    "station",
    "time_utc",
    "local_time",
    "variable",
    "value",
    "units",
    "period_h",
    "fg_depar",
    "an_depar",
    "qc",
    "report",
    "source",
]


def write_csv(rows: list[dict], path) -> None:
    fields = list(rows[0]) if rows and "variable" in rows[0] and "forecast" in rows[0] else None
    if fields is None:
        fields = [f for f in CSV_FIELDS if f != "local_time" or (rows and "local_time" in rows[0])]
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
    w.writeheader()
    for r in rows:
        w.writerow({k: "" if r.get(k) is None else r.get(k) for k in fields})
    if path in (None, "-"):
        sys.stdout.write(buf.getvalue())
    else:
        Path(path).write_text(buf.getvalue())


# --- access ---------------------------------------------------------------------------------------


def blocked(b: dict, as_json: bool = False, code: int = 4) -> int:
    """Report an access barrier: what blocks, why, what the user must do, what the agent does.

    Every legal or technical barrier gets this report (AGENTS.md "When access is blocked")."""
    lines = [f"BLOCKED: {b['blocked']}", f"Why: {b['why']}", "What the user needs to do:"]
    lines += [f"  {s}" for s in b["user_steps"]]
    lines.append(f"For the agent: {b['agent']}")
    print("\n".join(lines), file=sys.stderr)
    if as_json:
        print(json.dumps({"blocked": b}, indent=2))
    return code


def credentials(env=os.environ, home: Path | None = None) -> str | None:
    home = home or Path.home()
    if all(env.get(k) for k in ("ECMWF_API_KEY", "ECMWF_API_URL", "ECMWF_API_EMAIL")):
        return "ECMWF_API_* environment"
    if env.get("ECMWF_API_RC_FILE") and Path(env["ECMWF_API_RC_FILE"]).exists():
        return "ECMWF_API_RC_FILE"
    return "~/.ecmwfapirc" if (home / ".ecmwfapirc").exists() else None


ACCESS_STEPS = [
    "1. Create an ECMWF account and Web API key: log in at https://api.ecmwf.int/v1/key/ "
    "(it shows url, key and email; keys last one year).",
    '2. Save it as ~/.ecmwfapirc: {"url": "https://api.ecmwf.int/v1", "key": "<key>", '
    '"email": "<email>"} and run: chmod 600 ~/.ecmwfapirc',
    "3. Observations in MARS are restricted: reports (TYPE=OB) and feedback less than 24 h "
    "old need explicit rights. Users at Member and Co-operating State national weather "
    "services ask their Computing Representative "
    "(https://www.ecmwf.int/en/about/contact-us/computing-representatives); others need a "
    "service agreement (https://www.ecmwf.int/en/forecasts/accessing-forecasts/"
    "service-agreements). Questions: https://support.ecmwf.int",
]
NOAA_OFFER = (
    "Explain that ECMWF observations need MARS observation rights and relay the steps. Then "
    "ask whether the user wants the latest METARs (airports, last hours only) from NOAA "
    "Aviation Weather — a non-ECMWF source — via `obs.py series --station ICAO "
    "--latest-metar`; run it only after a yes and label the result as non-ECMWF. Do not retry "
    "MARS and never substitute another provider's data silently."
)


def access_barrier() -> dict:
    return {
        "blocked": "this account has no MARS rights for observations",
        "why": "MARS restricts observation reports (TYPE=OB) and recent feedback (OFB/MFB, less "
        "than 24 h old) to accounts granted access; a valid Web API key alone is not enough.",
        "user_steps": [ACCESS_STEPS[2], "4. Then re-run the same obs.py command."],
        "agent": NOAA_OFFER,
    }


def no_key_barrier() -> dict:
    return {
        "blocked": "no ECMWF Web API key on this machine",
        "why": "station observations come from ECMWF's MARS archive, which needs a Web API key "
        "and an account with observation rights.",
        "user_steps": [*ACCESS_STEPS, "4. Re-run the same obs.py command."],
        "agent": "Give these steps (never ask for the key in chat). `obs.py series --dry-run` "
        "still shows the requests. " + NOAA_OFFER,
    }


def noaa_barrier(error: str) -> dict:
    return {
        "blocked": "cannot reach NOAA Aviation Weather (aviationweather.gov)",
        "why": f"{error}. aviationweather.gov is only used for ICAO station metadata and, "
        "on request, the latest METARs; it is not an ECMWF service.",
        "user_steps": [
            "1. Use the station's WMO id instead of the ICAO id: python3 obs.py stations NAME "
            "searches the bundled WMO station index offline.",
            "2. Or check this machine can reach https://aviationweather.gov, then retry.",
        ],
        "agent": "Search the bundled index by name (`obs.py stations NAME`) and continue with "
        "the WMO id; do not fall back to other providers.",
    }


def deps_barrier(error: str) -> dict:
    return {
        "blocked": "the Python packages obs.py needs are not installed",
        "why": f"{error}. Decoding needs ecmwf-api-client, pyodc (ODB), pdbufr (BUFR), "
        "earthkit-utils/-meteo and thermofeel.",
        "user_steps": [
            "1. Install uv: https://docs.astral.sh/uv/getting-started/installation/ "
            "(curl -LsSf https://astral.sh/uv/install.sh | sh), then run `uv run obs.py …` — "
            "it installs exactly what the script declares.",
            "2. Without uv: pip install ecmwf-api-client pyodc pdbufr earthkit-utils "
            "'earthkit-meteo[scores]' thermofeel",
            "3. On Windows use WSL (eccodes and odc have no Windows wheels).",
        ],
        "agent": "Re-run with `uv run`; if uv can't be installed give these steps. "
        "`stations`, `--dry-run` and `--latest-metar` work with plain Python.",
    }


def barrier_for_error(message: str) -> dict | None:
    if ecmwf_status.NETWORK_ERROR.search(message):
        return ecmwf_status.network_barrier("webapi", message.splitlines()[-1][:200])
    if re.search(r"(?i)invalid (api )?key|key (has )?expired|\b401\b|authenti", message):
        return {
            "blocked": "the ECMWF Web API rejected the key",
            "why": "the key is mistyped, from another account, or expired (keys last a year).",
            "user_steps": [*ACCESS_STEPS[:2], "3. Re-run the same obs.py command."],
            "agent": "Give these steps; do not retry until the user has a fresh key.",
        }
    if re.search(
        r"(?i)no access|not authori[sz]ed|not allowed|permission|forbidden|\b403\b|restricted"
        r"|access denied",
        message,
    ):
        return access_barrier()
    return None


def _interrupt(signum, frame):
    raise KeyboardInterrupt(f"signal {signum}")


@contextlib.contextmanager
def _webapi_jobs_cancelled_on_interrupt():
    """ecmwf-api-client deletes its server-side job only after a normal finish. Interrupted
    (Ctrl-C, or SIGTERM from a timeout), the job stays queued or active on the server and the
    user's next requests wait behind it. Track the client's connections and delete their jobs."""
    try:
        from ecmwfapi import api
    except ImportError:  # local mars client: nothing queued on the Web API
        yield
        return
    conns, init = [], api.Connection.__init__

    def tracking_init(self, *a, **k):
        init(self, *a, **k)
        conns.append(self)

    api.Connection.__init__ = tracking_init
    old = signal.signal(signal.SIGTERM, _interrupt)
    try:
        yield
    except BaseException:
        for c in conns:
            c.cleanup()  # DELETE the job; the client swallows errors here
        raise
    finally:
        api.Connection.__init__ = init
        signal.signal(signal.SIGTERM, old)


def mars_retrieve(req: dict, target: Path) -> int:
    """Run one request through the ECMWF Web API; returns the bytes received (0 = no data)."""
    from ecmwfapi import ECMWFService

    with _webapi_jobs_cancelled_on_interrupt(), contextlib.redirect_stdout(sys.stderr):
        ECMWFService("mars", quiet=True, verbose=False).execute(mars_text(req), str(target))
    return target.stat().st_size if target.exists() else 0


# --- CLI ------------------------------------------------------------------------------------------


def _resolve_all(spec: str, fetch=_get_json) -> list[dict]:
    stations = load_stations()
    return [resolve_station(s, stations, fetch) for s in spec.split(",") if s.strip()]


def _period(a, today: date) -> tuple[date, date]:
    if a.days:
        return today - timedelta(days=a.days), today
    if not (a.start and a.end):
        raise ValueError("give --start and --end (YYYY-MM-DD), or --days N")
    start, end = parse_day(a.start, today), parse_day(a.end, today)
    if start > end:
        raise ValueError(f"--start {start} is after --end {end}")
    return start, end


def fetch_observations(sts, start, end, variables, raw, prefer, log=print) -> list[dict]:
    reqs = build_requests(sts, start, end, variables, raw=raw, prefer=prefer)
    for r in reqs:
        errs = lint_request(r)
        if errs:
            raise ValueError("internal request failed lint: " + "; ".join(errs))
    rows: list[dict] = []
    with tempfile.TemporaryDirectory(prefix="ecmwf-obs-") as tmp:
        for i, r in enumerate(reqs, 1):
            target = Path(tmp) / f"part{i}.{'bufr' if raw else 'odb'}"
            log(f"MARS request {i}/{len(reqs)}: {r['type']} {r['date']} {r['time']} …")
            size = mars_retrieve(r, target)
            log(f"  received {size} bytes")
            if size:
                rows += decode_bufr(target, sts) if raw else decode_feedback(target, sts)
    # the 00 UTC window starts at 21 UTC the day before: keep only the requested days
    lo, hi = start.isoformat(), (end + timedelta(days=1)).isoformat()
    return [r for r in _dedupe(rows) if r["variable"] in variables and lo <= r["time_utc"] < hi]


def _emit(payload: dict, rows: list[dict], a) -> None:
    out = a.output
    as_csv = a.csv or (out and str(out).endswith(".csv") and not a.json)
    as_json = a.json or (out and str(out).endswith(".json"))
    if as_csv:
        write_csv(rows, out)
        if out:
            print(f"saved {out} ({len(rows)} rows)\nsource: {payload['source']}")
            print(f"attribution: {payload['attribution']}")
        return
    if as_json:
        text = json.dumps({**payload, "rows": rows}, indent=2)
        if out:
            Path(out).write_text(text)
            print(f"saved {out} ({len(rows)} rows)\nsource: {payload['source']}")
        else:
            print(text)
        return
    print(f"Source: {payload['source']}")
    for sid, st in payload["stations"].items():
        print(f"Station {sid}: {st.get('name', '')} ({st.get('lat')}, {st.get('lon')})")
    for var, s in payload["summary"].items():
        line = (
            f"  {var:<22} n={s['n']:<4} min {s['min']['value']} ({s['min']['time_utc']})  "
            f"max {s['max']['value']} ({s['max']['time_utc']})"
        )
        line += f"  total {s['total']}" if "total" in s else f"  mean {s.get('mean')}"
        print(f"{line} {s['units']}")
    if len(rows) <= 60:
        for r in rows:
            when = r.get("local_time") or r["time_utc"]
            print(f"  {r['station']} {when} {r['variable']:<22} {r['value']} {r['units']}")
    else:
        print(f"  {len(rows)} rows — write them with --csv -o FILE or --json -o FILE")
    for n in payload["notes"]:
        print(f"note: {n}")
    print(f"Attribution: {payload['attribution']}")


def cmd_series(a, now: datetime) -> int:
    variables = parse_vars(a.vars)
    if a.latest_metar:
        rows = []
        for s in a.station.split(","):
            kind, ident = classify(s)
            if kind != "icao":
                raise ValueError("--latest-metar needs ICAO ids (e.g. EGLL)")
            rows += noaa_latest_metar(ident, a.hours)
        rows = [r for r in rows if r["variable"] in [*variables, "raw"]]
        sts = {}
        source, attribution, notes = NOAA_LABEL, ATTRIBUTION["noaa"], []
        if not rows:
            notes.append(f"no METAR from {a.station} in the last {a.hours} h")
    else:
        sts = {}
        resolved = _resolve_all(a.station)
        for st in resolved:
            st["_metar"] = report_kind(st, a.report) == "METAR"
            sts[st["icao"] if st["_metar"] else st["wmo"]] = st
        source, attribution = ECMWF_LABEL, ATTRIBUTION["ecmwf"]
        notes = []
        if a.file:
            path = Path(a.file)
            is_bufr = path.read_bytes()[:4] == b"BUFR"
            rows = decode_bufr(path, resolved) if is_bufr else decode_feedback(path, resolved)
            rows = [r for r in rows if r["variable"] in variables]
        else:
            start, end = _period(a, now.date())
            note = latency_note(end, a.raw, now)
            if note:
                notes.append(note)
            if a.dry_run:
                for r in build_requests(resolved, start, end, variables, a.raw, a.report):
                    print(mars_text(r) + "\n")
                    for e in lint_request(r):
                        print(f"ERROR: {e}")
                for n in notes:
                    print(f"note: {n}")
                return 0
            if not credentials():
                return blocked(no_key_barrier(), a.json)
            rows = fetch_observations(
                resolved,
                start,
                end,
                variables,
                a.raw,
                a.report,
                log=lambda m: print(m, file=sys.stderr),
            )
            if not rows:
                notes.append(
                    "MARS returned no observations: check the station id and dates (feedback "
                    "covers stations the analysis used; try --raw for all reports)."
                )
    if a.indices and rows:
        idx, inotes = compute_indices(rows)
        rows = sorted(rows + idx, key=lambda r: (r["station"], r["time_utc"], r["variable"]))
        notes += inotes
    if a.tz:
        add_local_time(rows, a.tz)
    payload = {
        "source": source,
        "dataset": "NOAA METAR" if a.latest_metar else ("BUFR" if a.raw else "ODB feedback"),
        "stations": {
            k: {x: v for x, v in s.items() if not x.startswith("_")} for k, s in sts.items()
        },
        "summary": summarise(rows),
        "notes": notes,
        "attribution": attribution,
    }
    _emit(payload, rows, a)
    return 0


def cmd_verify(a, now: datetime) -> int:
    forecast = json.loads(Path(a.forecast).read_text())
    st = _resolve_all(a.station)[0]
    if a.obs:
        rows = json.loads(Path(a.obs).read_text())["rows"]
    elif a.file:
        path = Path(a.file)
        is_bufr = path.read_bytes()[:4] == b"BUFR"
        rows = decode_bufr(path, [st]) if is_bufr else decode_feedback(path, [st])
    else:
        times = [parse_iso(r["valid_time"]) for r in forecast.get("series", [])]
        if not times:
            raise ValueError("the forecast JSON has no series (odpoint.py/ptpoint.py --json)")
        if not credentials():
            return blocked(no_key_barrier(), a.json)
        rows = fetch_observations(
            [st],
            min(times).date(),
            min(max(times).date(), now.date()),
            parse_vars("t2m,td,wind,mslp"),
            a.raw,
            "auto",
            log=lambda m: print(m, file=sys.stderr),
        )
    if a.indices:
        rows = rows + compute_indices(rows)[0]
    pairs = pair(forecast, rows, a.tolerance)
    result = {
        "station": {k: v for k, v in st.items() if not k.startswith("_")},
        "forecast": {"model": forecast.get("model"), "run": forecast.get("run")},
        "pairs": len(pairs),
        "gridpoint_distance_km": gridpoint_distance_km(forecast, st),
        "scores": scores(pairs) if pairs else {},
        "definition": "bias = mean(forecast - observed); MAE, RMSE over pairs matched by "
        f"valid time (nearest report within {a.tolerance} min)",
        "observations": ATTRIBUTION["ecmwf"]
        if not rows or rows[0]["source"].startswith("ECMWF")
        else rows[0]["source"],
    }
    if not pairs:
        result["note"] = "no forecast/observation pairs: check the station and the time range"
    if a.output:
        write_csv(pairs, a.output)
        result["pairs_csv"] = a.output
    if a.meteogram_json:
        Path(a.meteogram_json).write_text(json.dumps(with_observations(forecast, rows, st)))
        result["meteogram_json"] = a.meteogram_json
        result["plot"] = (
            f"uv run <ecmwf-earthkit>/scripts/ekplot.py meteogram {a.meteogram_json} -o FILE.png"
        )
    if a.json:
        print(json.dumps(result, indent=2))
    else:
        sid = result["station"].get("wmo") or result["station"].get("icao")
        print(f"Station {sid}: {len(pairs)} pairs ({result['definition']})")
        if result["gridpoint_distance_km"] is not None:
            print(f"  forecast gridpoint {result['gridpoint_distance_km']} km from the station")
        for var, s in result["scores"].items():
            o = s["overall"]
            print(f"  {var}: n={o['n']} bias={o['bias']} MAE={o['mae']} RMSE={o['rmse']}")
            for step, x in s["by_step"].items():
                print(f"    step {step}: bias={x['bias']} MAE={x['mae']}")
        for k in ("pairs_csv", "meteogram_json", "plot", "note"):
            if k in result:
                print(f"{k}: {result[k]}")
    return 0


def cmd_points(a) -> int:
    data = json.loads(Path(a.series).read_text())
    t = parse_iso(a.time)
    best: dict[str, dict] = {}
    for r in data["rows"]:
        if r["variable"] != a.variable:
            continue
        dt = abs(parse_iso(r["time_utc"]) - t)
        if dt <= timedelta(minutes=a.tolerance) and (
            r["station"] not in best or dt < abs(parse_iso(best[r["station"]]["time_utc"]) - t)
        ):
            best[r["station"]] = r
    pts = []
    for sid, r in sorted(best.items()):
        st = data.get("stations", {}).get(sid, {})
        if st.get("lat") is not None:
            where = {"name": st.get("name", ""), "lat": st["lat"], "lon": st["lon"]}
            pts.append({"station": sid, **where, "value": r["value"], "time_utc": r["time_utc"]})
    out = {
        "variable": a.variable,
        "units": UNITS.get(a.variable, ""),
        "time": iso(t),
        "source": data.get("source", ""),
        "points": pts,
    }
    Path(a.output).write_text(json.dumps(out, indent=2))
    overlay = f"ekplot.py map … --obs {a.output}"
    print(f"saved {a.output} ({len(pts)} stations, {out['units']}); overlay with {overlay}")
    return 0


def cmd_stations(a) -> int:
    kind, ident = classify(a.query)
    if kind == "icao":
        found = noaa_station_info(ident)
        hits = [found] if found else []
    else:
        hits = search_stations(a.query, load_stations())
        if not hits and kind == "wmo":
            found = oscar_station(ident)
            hits = [found] if found else []
    if a.json:
        print(json.dumps({"stations": hits}, indent=2))
    elif not hits:
        print(f"no station matches {a.query!r}")
    for h in [] if a.json else hits:
        print(
            f"{h['wmo'] or '-':>5} {h.get('icao') or '':<4} {h['name']:<40} "
            f"{h['lat']:>8} {h['lon']:>9} {h.get('elevation') or '':>6}  {h['territory']}  "
            f"[{h['source']}]"
        )
    return 0 if hits else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("stations", help="find a station by WMO id, ICAO id or name")
    s.add_argument("query")
    s.add_argument("--json", action="store_true")

    def common(p):
        p.add_argument("--station", required=True, help="WMO id(s), ICAO id(s) or a name")
        p.add_argument("--raw", action="store_true", help="BUFR reports instead of feedback")
        p.add_argument("--file", help="decode an existing ODB/BUFR file instead of retrieving")
        p.add_argument("--indices", action="store_true", help="add thermal-comfort indices")
        p.add_argument("--json", action="store_true")
        p.add_argument("-o", "--output", help="output file (.csv or .json)")

    s = sub.add_parser("series", help="observations at stations over a period")
    common(s)
    s.add_argument("--start", help="YYYY-MM-DD (or -N: N days ago)")
    s.add_argument("--end", help="YYYY-MM-DD (or -N)")
    s.add_argument("--days", type=int, help="the last N days up to now")
    s.add_argument("--vars", default=DEFAULT_VARS, help=f"comma list of {', '.join(GROUPS)}")
    s.add_argument("--report", default="auto", choices=["auto", "synop", "metar"])
    s.add_argument("--tz", help="IANA time zone for local_time, e.g. Europe/Lisbon")
    s.add_argument("--csv", action="store_true")
    s.add_argument("--dry-run", action="store_true", help="print the MARS requests only")
    s.add_argument(
        "--latest-metar",
        action="store_true",
        help=f"latest METARs from {NOAA_LABEL} — only when the user asks for them",
    )
    s.add_argument("--hours", type=int, default=3, help="with --latest-metar: hours back")
    v = sub.add_parser("verify", help="forecast versus observations at a station")
    common(v)
    v.add_argument("--forecast", required=True, help="odpoint.py / ptpoint.py --json output")
    v.add_argument("--obs", help="observations from `obs.py series --json -o FILE`")
    v.add_argument("--tolerance", type=int, default=30, help="minutes between report and time")
    v.add_argument("--meteogram-json", help="write forecast + observations for ekplot.py")
    p = sub.add_parser("points", help="one variable at one time from a series JSON, for maps")
    p.add_argument("series", help="obs.py series --json output (one or more stations)")
    p.add_argument("--variable", default="t2m")
    p.add_argument("--time", required=True, help="e.g. 2026-10-09T12:00Z")
    p.add_argument("--tolerance", type=int, default=30, help="minutes")
    p.add_argument("-o", "--output", required=True)
    return ap


def main(argv=None, now: datetime | None = None) -> int:
    a = build_parser().parse_args(argv)
    now = now or datetime.now(UTC)
    as_json = getattr(a, "json", False)
    try:
        if a.cmd == "stations":
            return cmd_stations(a)
        if a.cmd == "points":
            return cmd_points(a)
        if a.cmd == "series":
            return cmd_series(a, now)
        return cmd_verify(a, now)
    except NoaaUnavailable as e:
        return blocked(noaa_barrier(str(e)), as_json, code=2)
    except ImportError as e:
        return blocked(deps_barrier(str(e)), as_json, code=3)
    except (LookupError, ValueError, OSError, json.JSONDecodeError, KeyError) as e:
        b = barrier_for_error(str(e))
        if b:
            return blocked(b, as_json, code=2 if "cannot reach" in b["blocked"] else 4)
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # ecmwf-api-client / MARS errors
        b = barrier_for_error(str(e))
        if b:
            return blocked(b, as_json, code=2 if "cannot reach" in b["blocked"] else 4)
        print(f"error: {str(e).splitlines()[-1] if str(e) else repr(e)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
