#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.12"
# dependencies = [
#   "earthkit-data[polytope,covjsonkit]>=1.2",
#   "earthkit-meteo>=1.2",
#   "earthkit-utils>=1.0",
#   "earthkit-transforms[all]>=1.0",
#   "scipy>=1.16",
#   "thermofeel>=2.3",
# ]
# ///
"""Point forecast via ECMWF Polytope — server-side extraction, a few KB instead of global fields.

earthkit components, each for its job:
  earthkit-data[polytope]  submit the feature-extraction request (polytope-client)
  earthkit-meteo           wind speed / direction
  earthkit-utils           unit conversion
  earthkit-transforms      de-accumulation, ensemble percentiles, daily statistics
  thermofeel               feels-like indices (--indices; ECMWF's thermal-comfort library)

  uv run ptpoint.py --check                                  # credentials + test request
  python3 ptpoint.py --check --offline                       # collections list only
  uv run ptpoint.py --lat 38.72 --lon -9.14                  # IFS HRES, hourly, 0-240 h
  uv run ptpoint.py --lat 38.72 --lon -9.14 --steps 0-24 --json
  uv run ptpoint.py --lat 51.45 --lon -0.97 --ensemble --steps 0-120   # 50-member percentiles
  uv run ptpoint.py --lat 37.39 --lon -5.98 --next-hours 48 --indices   # feels-like columns

Output JSON matches the ecmwf-open-data skill's odpoint.py (plus *_p10/_p50/_p90 for --ensemble), so
the ecmwf-earthkit skill's `ekplot.py meteogram` plots it. Exit code 4 = no Polytope credentials:
use the ecmwf-open-data skill instead.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # heavy; imported inside the functions that need it
    import xarray as xr


# Never write __pycache__ into the installed skill (it may be read-only, and skills must
# not be modified); sibling scripts are imported below.
sys.dont_write_bytecode = True
ecmwf_status = importlib.import_module(
    "ecmwf_status"
)  # sibling script: what ECMWF says about service status

UTC = timezone.utc
ADDRESS = os.environ.get("POLYTOPE_ADDRESS", "polytope.ecmwf.int")
COLLECTION = "ecmwf-mars"
# The collections list is a small JSON document; 30 s tolerates a slow server.
COLLECTIONS_TIMEOUT_S = 30
# paramIds: 2t, tp, 10u, 10v, msl, tcc (ensemble: 2t, tp — enough for uncertainty, keeps it small)
PARAMS = "167/228/165/166/151/164"
ENS_PARAMS = "167/228"
# --indices adds 2 m dewpoint (168); ensemble requests also need 10 m wind (165/166).
INDEX_PARAMS = "168"
ENS_INDEX_PARAMS = "168/165/166"
# Operational runs are usable ~7 h after base time; Polytope keeps roughly the last 2 days.
AVAILABLE_AFTER = timedelta(hours=7)
MAX_CANDIDATES = 4
# IFS HRES 06/18 UTC runs reach 144 h (since IFS Cycle 50r1); 00/12 UTC runs reach 360 h.
SHORT_RUN_HOURS = 144
QUANTILES = (10, 50, 90)
UNITS = {
    "t2m_C": "degC",
    "precip_mm": "mm since previous step",
    "wind_speed_ms": "m/s",
    "wind_dir_deg": "degrees (from, meteorological)",
    "msl_hPa": "hPa",
    "tcc_pct": "%",
    "heat_index_C": "degC",
    "humidex_C": "degC",
    "apparent_temperature_C": "degC",
    "wind_chill_C": "degC",
}
INDEX_KEYS = ("heat_index_C", "humidex_C", "apparent_temperature_C", "wind_chill_C")
# Wind chill validity (thermofeel.calculate_wind_chill docstring): air temperature -50..5 °C and
# 10 m wind 5..80 km/h; outside it the formula is not a wind chill, so the value is left empty.
WIND_CHILL_T_C = (-50.0, 5.0)
WIND_CHILL_WIND_KMH = (5.0, 80.0)
INDICES_NOTE = (
    "Feels-like indices from thermofeel (ECMWF), no radiation: heat index (NOAA, adjusted; "
    "meaningful above ~27 °C), humidex (Environment Canada; above ~20 °C), apparent temperature "
    "(Steadman, shade), wind chill (only for -50..5 °C and 5..80 km/h 10 m wind, else empty)."
)


# --- credentials ------------------------------------------------------------------------------


# Names reported from a broken credentials file: identifier-like only, so a value that happens
# to sit on a line of its own (a pasted key) is never echoed as a "name".
KEY_NAME = re.compile(r"[A-Za-z_]{1,24}")


class MalformedCredentials(ValueError):
    """A credentials source exists but cannot be used. Carries key names, never values."""

    def __init__(self, source: str, problem: str, keys=(), layout: str = "polytope"):
        self.source, self.problem, self.layout = source, problem, layout
        self.keys = sorted({str(k) for k in keys if KEY_NAME.fullmatch(str(k))})
        super().__init__(f"{source} is malformed: {problem}")


def _user_path(value: str, home: Path) -> Path:
    """Expand a leading ~ against `home` (polytope-client expands it against the real home)."""
    v = str(value)
    if v == "~" or v.startswith("~/"):
        return home / v[2:]
    return Path(v)


def _read_text(path: Path, label: str, layout: str) -> str:
    try:
        raw = path.read_bytes()
    except OSError as e:
        problem = f"cannot be read ({e.strerror or type(e).__name__})"
        raise MalformedCredentials(label, problem, layout=layout) from None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise MalformedCredentials(label, "is not UTF-8 text", layout=layout) from None


def _text_key_names(text: str) -> list[str]:
    return re.findall(r"(?m)^\s*([A-Za-z_]{1,24})\s*[:=]", text)


def _json_object(path: Path, label: str, layout: str) -> dict:
    text = _read_text(path, label, layout)
    if not text.strip():
        raise MalformedCredentials(label, "is empty", layout=layout)
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        names = _text_key_names(text)
        hint = " (it looks like a 'key: value' file)" if names else ""
        raise MalformedCredentials(label, f"is not JSON{hint}", names, layout) from None
    if not isinstance(d, dict):
        problem = f"holds a JSON {type(d).__name__}, not an object"
        raise MalformedCredentials(label, problem, layout=layout)
    return d


def _yaml_config(path: Path, label: str) -> dict:
    """Top-level 'name: value' pairs of polytope-client's config.yaml (standard library only:
    the file is flat — address, user_key, user_email, key_path, ...)."""
    out = {}
    for line in _read_text(path, label, "yaml").splitlines():
        m = re.match(r"([A-Za-z_]\w*)\s*:\s*(.*?)\s*$", line)
        if m and not line[:1].isspace():
            v = re.sub(r"\s+#.*$", "", m[2])
            if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
                v = v[1:-1]
            out[m[1]] = v
        elif line.strip() and not line.lstrip().startswith(("#", "-")) and not line[:1].isspace():
            raise MalformedCredentials(label, "is not a YAML mapping", out.keys(), "yaml")
    return out


def _text_value(d: dict, name: str) -> str | None:
    v = d.get(name)
    return v if isinstance(v, str) and v.strip() else None


def _locate(env, home: Path) -> tuple[str | None, str | None, str | None]:
    """(source name, key, email) in polytope-client's order: POLYTOPE_USER_KEY/EMAIL, the
    config.yaml in ~/.polytope-client (or POLYTOPE_CONFIG_PATH), the key file ~/.polytopeapirc
    (or POLYTOPE_KEY_PATH / key_path; user_key, user_email), then ~/.ecmwfapirc (key, email).
    Raises MalformedCredentials where the first source found is unusable."""
    key, email = env.get("POLYTOPE_USER_KEY"), env.get("POLYTOPE_USER_EMAIL")
    if key:
        return "POLYTOPE_USER_KEY", key, email
    if env.get("POLYTOPE_CONFIG_PATH"):
        cfg_dir, cfg_label = _user_path(env["POLYTOPE_CONFIG_PATH"], home), "POLYTOPE_CONFIG_PATH"
        cfg_label += "/config.yaml"
    else:
        cfg_dir, cfg_label = home / ".polytope-client", "~/.polytope-client/config.yaml"
    cfg = (
        _yaml_config(cfg_dir / "config.yaml", cfg_label)
        if (cfg_dir / "config.yaml").is_file()
        else {}
    )
    if _text_value(cfg, "user_key"):
        return cfg_label, cfg["user_key"], email or _text_value(cfg, "user_email")
    if env.get("POLYTOPE_KEY_PATH"):
        key_file, label = _user_path(env["POLYTOPE_KEY_PATH"], home), "POLYTOPE_KEY_PATH"
    elif _text_value(cfg, "key_path"):
        key_file, label = _user_path(cfg["key_path"], home), f"key_path in {cfg_label}"
    else:
        key_file, label = home / ".polytopeapirc", "~/.polytopeapirc"
    if key_file.is_file():
        d = _json_object(key_file, label, "polytope")
        if not _text_value(d, "user_key"):
            hint = " (key/email are ~/.ecmwfapirc's names)" if "key" in d else ""
            raise MalformedCredentials(label, f"has no user_key{hint}", d.keys(), "polytope")
        return label, d["user_key"], _text_value(d, "user_email")
    rc = home / ".ecmwfapirc"
    if rc.is_file():
        d = _json_object(rc, "~/.ecmwfapirc", "ecmwf")
        if not _text_value(d, "key"):
            hint = " (user_key belongs in ~/.polytopeapirc)" if "user_key" in d else ""
            raise MalformedCredentials("~/.ecmwfapirc", f"has no key{hint}", d.keys(), "ecmwf")
        return "~/.ecmwfapirc", d["key"], _text_value(d, "email")
    return None, None, None


def credentials(env=None, home: Path | None = None) -> str | None:
    """Where polytope-client will find a key (name only — never the value). A malformed source
    is still named here; auth_header() reports what is wrong with it."""
    env = os.environ if env is None else env
    try:
        return _locate(env, home or Path.home())[0]
    except MalformedCredentials as e:
        return e.source


def malformed_barrier(e: MalformedCredentials) -> dict:
    """BLOCKED report for a credentials source that exists but is unusable (names only)."""
    keys = ", ".join(e.keys) or "none readable"
    if e.layout == "yaml":
        fix = (
            f"2. Fix {e.source}: one 'name: value' per line, e.g.\n"
            "   user_key: <key>\n   user_email: <email>\n"
            "   (or delete those two lines and use ~/.polytopeapirc)"
        )
    elif e.layout == "ecmwf":
        fix = (
            "2. Rewrite ~/.ecmwfapirc as JSON, then run: chmod 600 ~/.ecmwfapirc\n"
            '   {"url": "https://api.ecmwf.int/v1", "key": "<key>", "email": "<email>"}\n'
            '   — or create ~/.polytopeapirc with {"user_email": "<email>", "user_key": "<key>"}'
        )
    else:
        f = "~/.polytopeapirc" if e.source == "~/.polytopeapirc" else "that file"
        fix = (
            f"2. Rewrite {f} as JSON, then run: chmod 600 {f}\n"
            '   {"user_email": "<email>", "user_key": "<key>"}'
        )
    return {
        "blocked": f"{e.source} is malformed",
        "why": f"it {e.problem}; keys found: {keys}. polytope-client cannot use it either.",
        "user_steps": [
            "1. Log in and open https://api.ecmwf.int/v1/key/ — it shows your key and email.",
            fix,
            '3. Polytope reads "user_key"/"user_email"; ~/.ecmwfapirc (MARS) uses "url"/"key"/'
            '"email". One file may contain both layouts.',
            "4. Re-run: uv run ptpoint.py --check",
        ],
        "agent": "Answer from the ecmwf-open-data skill now and relay these steps; never open "
        "or print the file and never ask for the key in chat.",
    }


# --- request ------------------------------------------------------------------------------------


def build_request(
    lat: float,
    lon: float,
    date: str,
    time: str,
    end_step: int = 240,
    ensemble: bool = False,
    members: str = "1/to/50",
    indices: bool = False,
) -> dict:
    param = ENS_PARAMS if ensemble else PARAMS
    if indices:
        param += "/" + (ENS_INDEX_PARAMS if ensemble else INDEX_PARAMS)
    r = {
        "class": "od",
        "stream": "enfo" if ensemble else "oper",
        "type": "pf" if ensemble else "fc",
        "date": date,
        "time": time,
        "levtype": "sfc",
        "expver": "0001",
        "domain": "g",
        "param": param,
        "feature": {
            "type": "timeseries",
            "points": [[lat, lon]],
            "axes": ["latitude", "longitude"],
            "time_axis": "step",
            "range": {"start": 0, "end": end_step},
        },
    }
    if ensemble:
        r["number"] = members
    return r


def candidate_runs(now: datetime | None = None, end_step: int = 240) -> list[tuple[str, str]]:
    """Runs to try, latest first. 06/18 UTC runs (to SHORT_RUN_HOURS) count when they cover
    the requested range — they are up to 6 h fresher than the 00/12 UTC runs."""
    now = (now or datetime.now(UTC)) - AVAILABLE_AFTER
    every = 6 if end_step <= SHORT_RUN_HOURS else 12
    t = now.replace(minute=0, second=0, microsecond=0, hour=now.hour - now.hour % every)
    out = []
    for _ in range(MAX_CANDIDATES):
        out.append((t.strftime("%Y%m%d"), t.strftime("%H00")))
        t -= timedelta(hours=every)
    return out


# --- parsing --------------------------------------------------------------------------------------


def _norm_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def parse_covjson(d: dict) -> dict:
    covs = d["coverages"]
    axes = covs[0]["domain"]["axes"]
    run = datetime.strptime(covs[0]["mars:metadata"]["Forecast date"], "%Y-%m-%dT%H:%M:%SZ")
    members = {}
    for c in covs:
        n = int(c["mars:metadata"].get("number", 0))
        members[n] = {p: r["values"] for p, r in c["ranges"].items()}
    times = axes["t"]["values"]
    return {
        "run": run.strftime("%Y-%m-%dT%H:%MZ"),
        "gridpoint": {
            "lat": round(axes["latitude"]["values"][0], 4),
            "lon": round(_norm_lon(axes["longitude"]["values"][0]), 4),
        },
        "times": [t.replace(":00:00Z", ":00Z") for t in times],
        "steps": [
            int((datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ") - run).total_seconds() // 3600)
            for t in times
        ],
        "members": members,
        "metadata": covs[0]["mars:metadata"],
    }


def _hourly(values, hours) -> xr.DataArray:
    """1-D series on a datetime axis, as earthkit-transforms expects (only spacing matters)."""
    import numpy as np
    import xarray as xr

    t = np.datetime64("2000-01-01T00", "ns") + np.asarray(hours, "timedelta64[h]")
    return xr.DataArray(np.asarray(values, float), dims=["valid_time"], coords={"valid_time": t})


def deaccumulate_mm(values_m: list[float], steps: list[int] | None = None) -> list[float | None]:
    """Accumulated precipitation (m since step 0) -> mm per interval since the previous step,
    with earthkit-transforms. The first value is 0 at step 0; unknown (None) when the series
    starts later (its value covers the whole run so far)."""
    from earthkit.transforms import temporal

    steps = list(range(len(values_m))) if steps is None else steps
    first = 0.0 if steps[0] == 0 else None
    if len(values_m) < 2:
        return [first]
    d = temporal.deaccumulate(_hourly(values_m, steps)).values * 1000.0
    return [first, *(round(max(0.0, float(v)), 2) for v in d)]


def percentile(values, q: float) -> float:
    """q-th percentile across ensemble members, with earthkit-transforms."""
    import earthkit.transforms as ekt
    import numpy as np
    import xarray as xr

    da = xr.DataArray(np.asarray(values, float), dims=["member"])
    return round(float(ekt.reduce(da, how="percentile", dim="member", q=q)), 4)


def quantiles(member_series: list[list[float]], qs=QUANTILES) -> list[list[float]]:
    """members x times -> times x quantiles (earthkit-transforms, over the member dimension)."""
    import earthkit.transforms as ekt
    import numpy as np
    import xarray as xr

    da = xr.DataArray(np.asarray(member_series, float), dims=["member", "valid_time"])
    per_q = [ekt.reduce(da, how="percentile", dim="member", q=q).values for q in qs]
    return [[round(float(v[i]), 4) for v in per_q] for i in range(da.sizes["valid_time"])]


def _deaccumulate_mm(vals: list[float], steps: list[int] | None = None) -> list[float]:
    return [0.0 if v is None else v for v in deaccumulate_mm(vals, steps)]


def feels_like(t2_k, td_k, u, v) -> dict:
    """Feels-like indices in °C from 2 m temperature and dewpoint (K) and 10 m wind (m/s).

    thermofeel computes every index (SI in, K out); earthkit-meteo the wind speed and
    earthkit-utils the K -> °C conversion. Wind chill is NaN outside its validity range."""
    import numpy as np
    import thermofeel as tf
    from earthkit.meteo import wind
    from earthkit.utils.units import convert_units

    t2_k, td_k = np.asarray(t2_k, float), np.asarray(td_k, float)
    va = wind.speed(np.asarray(u, float), np.asarray(v, float))
    rh = tf.calculate_relative_humidity_percent(t2_k, td_k)
    kelvin = {
        "heat_index_C": tf.calculate_heat_index_adjusted(t2_k, td_k),
        "humidex_C": tf.calculate_humidex(t2_k, td_k),
        "apparent_temperature_C": tf.calculate_apparent_temperature(t2_k, va, rh),
        "wind_chill_C": tf.calculate_wind_chill(t2_k, va),
    }
    out = {
        k: convert_units(np.asarray(x, float), "degC", source_units="K") for k, x in kelvin.items()
    }
    t_c = convert_units(t2_k, "degC", source_units="K")
    kmh = convert_units(va, "km/h", source_units="m/s")
    valid = (
        (t_c >= WIND_CHILL_T_C[0])
        & (t_c <= WIND_CHILL_T_C[1])
        & (kmh >= WIND_CHILL_WIND_KMH[0])
        & (kmh <= WIND_CHILL_WIND_KMH[1])
    )
    out["wind_chill_C"] = np.where(valid, out["wind_chill_C"], np.nan)
    return out


def _cell(v, ndigits: int = 1):
    """Rounded value for the JSON rows; NaN (no valid index) becomes None."""
    return None if v != v else round(float(v), ndigits)


def size_note(nbytes: int) -> str:
    kb = nbytes / 1024
    return (
        f"Extracted server-side by ECMWF Polytope — {kb:.1f} KB transferred (the same series "
        "from Open Data means downloading ~100-300 MB of global fields)."
    )


def attribution(d: dict, year: int | None = None) -> dict:
    year = year or datetime.now(UTC).year
    cls = d["coverages"][0]["mars:metadata"].get("class")
    if cls == "ai":  # the Open Data section of Polytope
        return {
            "short": f"Data: © {year} ECMWF, CC BY 4.0",
            "licence": "CC-BY-4.0",
            "note": "ECMWF Open Data — CC BY 4.0 licence.",
        }
    return {
        "short": f"Data: © {year} ECMWF",
        "licence": "ECMWF licence",
        "note": "Operational ECMWF data under your organisation's ECMWF licence — check the "
        "licence before redistributing or publishing.",
    }


# --- local time and daily summaries (standard library) ------------------------------------------


def _utc(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


def add_local_time(rows: list[dict], tz: str) -> list[dict]:
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(tz)
    for r in rows:
        r["local_time"] = _utc(r["valid_time"]).astimezone(zone).isoformat(timespec="minutes")
    return rows


def drop_past(rows: list[dict], now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(UTC)
    return [r for r in rows if _utc(r["valid_time"]) >= now]


def daily_summary(p: dict, tz: str = "UTC") -> list[dict]:
    """Per local calendar day: high/low 2 m temperature (°C) and precipitation total (mm).

    Daily statistics with earthkit-transforms on a local-time axis (exact across DST changes).
    Ensembles give p10/p50/p90 of each member's daily high, low and total — the spread of the
    days, not of individual hours. A precipitation interval counts for the day it ends in.
    Days whose samples span less than 18 h are marked partial.
    """
    from zoneinfo import ZoneInfo

    import numpy as np
    import xarray as xr
    from earthkit.transforms import temporal

    zone = ZoneInfo(tz)
    local = [_utc(t).astimezone(zone) for t in p["times"]]
    axis = np.array([t.replace(tzinfo=None) for t in local], dtype="datetime64[ns]")
    names = list(p["members"])

    def stack(key, fn):
        rows = [fn(p["members"][n][key]) for n in names]
        return xr.DataArray(
            np.asarray(rows, float), dims=["member", "valid_time"], coords={"valid_time": axis}
        )

    has_t, has_tp = ("2t" in p["members"][names[0]]), ("tp" in p["members"][names[0]])
    stats = {}
    if has_t:
        t = stack("2t", lambda v: np.asarray(v) - 273.15)
        stats["high_C"] = temporal.daily_max(t, time_dim="valid_time")
        stats["low_C"] = temporal.daily_min(t, time_dim="valid_time")
    if has_tp:
        rain = stack("tp", lambda v: _deaccumulate_mm(v, p["steps"]))
        stats["precip_mm"] = temporal.daily_sum(
            rain.isel(valid_time=slice(1, None)), time_dim="valid_time"
        )
    days = sorted({t.date() for t in local})
    ensemble = names != [0]
    result = []
    for d in days:
        on_day = [t for t in local if t.date() == d]
        row: dict = {
            "date": d.isoformat(),
            "partial": (on_day[-1] - on_day[0]).total_seconds() / 3600 < 18,
        }
        key = np.datetime64(d.isoformat(), "ns")
        for name, da in stats.items():
            if key not in da["valid_time"].values:
                if name == "precip_mm":
                    vals = [0.0] * len(names)  # no interval ends on this day
                else:
                    continue
            else:
                vals = da.sel(valid_time=key).values.tolist()
            if ensemble:
                for q in QUANTILES:
                    row[f"{name}_p{q}"] = round(percentile(vals, q), 1)
            else:
                row[name] = round(float(vals[0]), 1)
        if ensemble:
            row["members"] = len(names)
        result.append(row)
    return result


# A run is published ~7 h after its base time and replaced 6 h later, so the newest run can
# be up to ~13 h old; request this much extra lead time for "the next N hours".
MAX_RUN_AGE_H = 14


def steps_for_next_hours(hours: int) -> int:
    return -(-(hours + MAX_RUN_AGE_H) // 6) * 6


def window(rows: list[dict], now: datetime | None = None, hours: int | None = None) -> list[dict]:
    """Rows from now (inclusive) to now + hours (exclusive)."""
    now = now or datetime.now(UTC)
    end = now + timedelta(hours=hours) if hours else None
    return [
        r
        for r in rows
        if _utc(r["valid_time"]) >= now and (end is None or _utc(r["valid_time"]) < end)
    ]


def shape_output(
    rows: list[dict],
    p: dict,
    tz: str | None = None,
    from_now: bool = False,
    daily: bool = False,
    now: datetime | None = None,
    next_hours: int | None = None,
) -> dict:
    """Optional local times, dropping past rows and per-day summaries for the JSON output."""
    out: dict = {}
    rows = [dict(r) for r in rows]  # never modify the caller's rows
    if tz:
        add_local_time(rows, tz)
        out["timezone"] = tz
    if next_hours:
        out["series"] = window(rows, now, next_hours)
    else:
        out["series"] = drop_past(rows, now) if from_now else rows
    if daily:
        out["daily"] = daily_summary(p, tz or "UTC")
    return out


# --- earthkit-backed ------------------------------------------------------------------------------


def series(p: dict) -> list[dict]:
    import numpy as np
    from earthkit.meteo import wind
    from earthkit.utils.units import convert_units

    rows = [{"step": s, "valid_time": t} for s, t in zip(p["steps"], p["times"], strict=True)]
    mem = p["members"]
    if list(mem) == [0]:  # deterministic
        m = mem[0]
        if "2t" in m:
            for r, v in zip(
                rows, convert_units(np.array(m["2t"]), "degC", source_units="K"), strict=True
            ):
                r["t2m_C"] = round(float(v), 1)
        if "tp" in m:
            for r, v in zip(rows, _deaccumulate_mm(m["tp"], p["steps"]), strict=True):
                r["precip_mm"] = v
        if "10u" in m and "10v" in m:
            u, v = np.array(m["10u"]), np.array(m["10v"])
            speeds, dirs = wind.speed(u, v), wind.direction(u, v, convention="meteo")
            for r, s, dd in zip(rows, speeds, dirs, strict=True):
                r["wind_speed_ms"], r["wind_dir_deg"] = (
                    round(float(s), 1),
                    round(float(dd), 1),
                )
        if "msl" in m:
            for r, v in zip(
                rows, convert_units(np.array(m["msl"]), "hPa", source_units="Pa"), strict=True
            ):
                r["msl_hPa"] = round(float(v), 1)
        if "tcc" in m:
            for r, v in zip(rows, m["tcc"], strict=True):
                r["tcc_pct"] = round(float(v) * 100.0, 1)
        if all(k in m for k in ("2t", "2d", "10u", "10v")):
            for key, vals in feels_like(m["2t"], m["2d"], m["10u"], m["10v"]).items():
                for r, v in zip(rows, vals, strict=True):
                    r[key] = _cell(v)
        return rows

    names = sorted(mem)
    t2 = [list(convert_units(np.array(mem[n]["2t"]), "degC", source_units="K")) for n in names]
    tp = (
        [_deaccumulate_mm(mem[n]["tp"], p["steps"]) for n in names]
        if "tp" in mem[names[0]]
        else None
    )
    for r, q in zip(rows, quantiles(t2), strict=True):
        r.update({f"t2m_C_p{k}": round(v, 1) for k, v in zip(QUANTILES, q, strict=True)})
        r["t2m_C"] = r["t2m_C_p50"]
        r["members"] = len(names)
    if tp:
        for r, q in zip(rows, quantiles(tp), strict=True):
            r.update({f"precip_mm_p{k}": round(v, 2) for k, v in zip(QUANTILES, q, strict=True)})
            r["precip_mm"] = r["precip_mm_p50"]
    if all(k in mem[names[0]] for k in ("2d", "10u", "10v")):
        per_member = [
            feels_like(mem[n]["2t"], mem[n]["2d"], mem[n]["10u"], mem[n]["10v"]) for n in names
        ]
        for key in INDEX_KEYS:
            stack = np.array([m[key] for m in per_member])
            # A percentile over only the members inside the validity range would misstate the
            # spread: give wind chill percentiles only where every member has a value.
            undefined = np.isnan(stack).any(axis=0)
            for r, q, skip in zip(rows, quantiles(np.nan_to_num(stack)), undefined, strict=True):
                r.update(
                    {
                        f"{key}_p{k}": None if skip else round(v, 1)
                        for k, v in zip(QUANTILES, q, strict=True)
                    }
                )
                r[key] = r[f"{key}_p50"]
    return rows


def fetch(req: dict) -> tuple[dict, int]:
    import earthkit.data as ekd

    with contextlib.redirect_stdout(sys.stderr):
        ds = ekd.from_source("polytope", COLLECTION, req, stream=False, address=ADDRESS)
    raw = Path(ds.path).read_bytes()
    return json.loads(raw), len(raw)


def fetch_latest(
    lat, lon, end_step, ensemble, date=None, time=None, indices=False
) -> tuple[dict, int]:
    runs = [(date, time)] if date else candidate_runs(end_step=end_step)
    last = None
    for d, t in runs:
        try:
            return fetch(build_request(lat, lon, d, t, end_step, ensemble, indices=indices))
        except (
            Exception
        ) as e:  # polytope-client raises generic errors; retry older runs on "no data"
            msg = str(e)
            if INSUFFICIENT.search(msg):  # an older run is refused the same way
                raise RuntimeError(f"Polytope: {msg.splitlines()[-1]}") from e
            if "__class__ must be set to a class" in msg:  # polytope-client crashing on a 401
                raise RuntimeError("Polytope rejected the key (HTTP 401)") from e
            if any(k in msg for k in ("401", "403", "Unauthorized", "Forbidden")):
                raise RuntimeError(
                    "Polytope rejected the credentials (no access for this account) — "
                    "use the ecmwf-open-data skill instead"
                ) from e
            last = msg.splitlines()[-1] if msg else repr(e)
    raise RuntimeError(f"no run found among {runs}: {last}")


# --- CLI ------------------------------------------------------------------------------------------


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


# The answer Polytope gives (HTTP 400) when the account may list the collection but not read it.
INSUFFICIENT = re.compile(r"(?i)insufficient permissions")


def entitlement_barrier() -> dict:
    return {
        "blocked": "this ECMWF account has no entitlement to operational forecast data",
        "why": "Polytope accepted the key — and lists the 'ecmwf-mars' collection to every "
        "user — but refused the data request with 'insufficient permissions': operational IFS "
        "data is licensed per account.",
        "user_steps": [
            "1. Users at a Member or Co-operating State national meteorological service: ask "
            "your Computing Representative "
            "(https://www.ecmwf.int/en/about/contact-us/computing-representatives) to grant "
            "your ECMWF account access to operational data.",
            "2. Everyone else: operational data needs an ECMWF licence or service agreement "
            "(https://www.ecmwf.int/en/forecasts/accessing-forecasts/service-agreements); "
            "questions: https://support.ecmwf.int",
            "3. Once granted, re-run: uv run ptpoint.py --check",
        ],
        "agent": "Answer now with the ecmwf-open-data skill (uv run "
        "<ecmwf-open-data>/scripts/odpoint.py --lat LAT --lon LON: the same IFS model, free) "
        "and give these steps; do not retry Polytope until the user confirms access.",
    }


def key_rejected_barrier(detail: str = "") -> dict:
    return {
        "blocked": "Polytope rejected the key",
        "why": f"{detail + ': ' if detail else ''}the key is mistyped, from another account, "
        "or expired (ECMWF keys last a year).",
        "user_steps": [*setup_steps()[:3], "4. Re-run: uv run ptpoint.py --check"],
        "agent": "Answer from the ecmwf-open-data skill now and give these steps; never ask "
        "for the key in chat; do not retry until the user has a fresh key.",
    }


def barrier_for_error(message: str) -> dict | None:
    if ecmwf_status.NETWORK_ERROR.search(message):
        return ecmwf_status.network_barrier("polytope", message.splitlines()[-1][:200])
    if INSUFFICIENT.search(message):
        return entitlement_barrier()
    if re.search(r"(?i)\b401\b|unauthori[sz]ed|rejected the key", message):
        return key_rejected_barrier()
    if re.search(r"(?i)\b403\b|forbidden|rejected the credentials", message):
        return {
            "blocked": "Polytope refused this account",
            "why": "the key is invalid, or the requested dataset is restricted (Member and "
            "Co-operating States, commercial data clients, research licence) or not available "
            "via Polytope.",
            "user_steps": setup_steps(),
            "agent": "Answer from the ecmwf-open-data skill now (odpoint.py) and give these steps; "
            "do not retry Polytope until the user confirms access.",
        }
    return None


def setup_steps() -> list[str]:
    """Exact steps to obtain ECMWF Polytope credentials (verified 2026-10)."""
    return [
        "1. Create an ECMWF account: open https://api.ecmwf.int/v1/key/, click Login, then "
        "'Register new user'.",
        "2. Log in and open https://api.ecmwf.int/v1/key/ — copy your key and email.",
        "3. Save ~/.polytopeapirc and run: chmod 600 ~/.polytopeapirc\n"
        '   {"user_email": "<email>", "user_key": "<key>"}\n'
        "   (without this file Polytope falls back to ~/.ecmwfapirc; or set POLYTOPE_USER_EMAIL "
        "and POLYTOPE_USER_KEY)",
        "4. Any authenticated ECMWF user can use Polytope. Some datasets are restricted to "
        "Member and Co-operating States, ECMWF commercial data clients, and researchers under a "
        "research licence (see https://www.ecmwf.int/en/forecasts/datasets); not all datasets "
        "are available via Polytope. For access to restricted data ask "
        "https://support.ecmwf.int (Member/Co-operating State users: your Computing "
        "Representative). Meanwhile, use the ecmwf-open-data skill.",
        "5. Check: uv run ptpoint.py --check",
        "Destination Earth Digital Twin data uses a different account and token: "
        "the ecmwf-destine skill.",
    ]


class AccessDenied(Exception):
    """The Polytope server rejected the credentials (401/403)."""


def auth_header(env=None, home: Path | None = None) -> str | None:
    """Authorization header from the same sources, in the same order, as polytope-client —
    never printed. None without credentials; MalformedCredentials for an unusable source."""
    env = os.environ if env is None else env
    _, key, email = _locate(env, home or Path.home())
    if not key:
        return None
    return f"EmailKey {email}:{key}" if email else f"Bearer {key}"


def _get_json(url: str, headers: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "ecmwf-weather-skills", **headers})
    try:
        with urllib.request.urlopen(req, timeout=COLLECTIONS_TIMEOUT_S) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def list_collections(address: str, auth: str, fetch=_get_json) -> list[str]:
    """GET /api/v1/collections: the data collections this account may use (authenticated)."""
    status, body = fetch(f"https://{address}/api/v1/collections", {"Authorization": auth})
    if status in (401, 403):
        raise AccessDenied(f"HTTP {status}: Polytope rejected these credentials")
    if status != 200:
        raise RuntimeError(f"collections endpoint answered HTTP {status}")
    return list(body.get("message", []))


def collection_barrier(collection: str, available: list[str]) -> dict:
    return {
        "blocked": f"this account has no access to the Polytope collection '{collection}'",
        "why": f"the account can use: {', '.join(available) or 'no collections'}.",
        "user_steps": [setup_steps()[3], "Then re-run: uv run ptpoint.py --check"],
        "agent": "Answer from the ecmwf-open-data skill now and give these steps; don't retry "
        "Polytope until the collection is listed.",
    }


# --- the check's test request: one real feature extraction over plain HTTP ----------------------
# polytope-client is not used here: it retries 10 times with 120 s sleeps on server errors and
# crashes on an expired key, which would turn a seconds-long check into minutes or a traceback.

# Reading (ECMWF headquarters): a land gridpoint present in every run.
TEST_POINT = (51.45, -0.97)
# A one-point, one-step extraction is ready in seconds; past a minute report the service as slow.
TEST_POLL_S = 60
POLL_EVERY_S = 1.0
HTTP_TIMEOUT_S = 30
NO_DATA = re.compile(
    r"(?i)not (yet )?released|no data|not found|not available|nothing to retrieve|no matching"
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Redirects are followed by hand: the key is only sent back to the Polytope host."""

    def redirect_request(self, *args, **kwargs):
        return None


def _http(method: str, url: str, headers: dict, body: bytes | None = None):
    """(status, lower-cased headers, body bytes) — HTTP errors and redirects are answers too."""
    opener = urllib.request.build_opener(_NoRedirect)
    req = urllib.request.Request(
        url, data=body, method=method, headers={"User-Agent": "ecmwf-weather-skills", **headers}
    )
    try:
        with opener.open(req, timeout=HTTP_TIMEOUT_S) as r:
            return r.status, {k.lower(): v for k, v in r.headers.items()}, r.read()
    except urllib.error.HTTPError as e:
        hdrs = {k.lower(): v for k, v in (e.headers or {}).items()}
        return e.code, hdrs, e.read() or b""


def test_request_body(date: str, time_: str) -> bytes:
    """What polytope-client POSTs for ptpoint's own request, cut to 2 m temperature at step 0.
    The request travels as text (polytope-client sends YAML; JSON is valid YAML)."""
    req = build_request(*TEST_POINT, date, time_, end_step=0)
    req["param"] = "167"
    return json.dumps({"verb": "retrieve", "request": json.dumps(req)}).encode()


def _message(body: bytes) -> str:
    try:
        d = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return body[:300].decode("utf-8", "replace").strip()
    return str(d.get("message", d) if isinstance(d, dict) else d)[:300]


def _classify(status: int, message: str) -> str:
    if status in (401, 403):
        return "key"
    if INSUFFICIENT.search(message):
        return "entitlement"
    if status in (400, 404) and NO_DATA.search(message):
        return "no_data"
    if status == 429 or status >= 500:
        return "network"
    return "error"


def _one_test_request(address: str, auth: str, run, http, sleep) -> dict:
    submit = f"https://{address}/api/v1/requests/{COLLECTION}"
    headers = {"Authorization": auth, "Content-Type": "application/json"}
    job = None

    def key_for(url: str) -> dict:  # the key goes back to the Polytope host only
        same = urllib.parse.urlsplit(url).netloc == address and url.startswith("https://")
        return {"Authorization": auth} if same else {}

    try:
        status, hdrs, body = http("POST", submit, headers, test_request_body(*run))
        if status == 202:
            job = urllib.parse.urljoin(submit, hdrs.get("location", ""))
            deadline = time.monotonic() + TEST_POLL_S
            while status == 202:
                if time.monotonic() > deadline:
                    return {"result": "network", "error": f"no result within {TEST_POLL_S} s"}
                sleep(POLL_EVERY_S)
                status, hdrs, body = http("GET", job, key_for(job))
        if status in (301, 302, 303, 307):
            url = urllib.parse.urljoin(job or submit, hdrs.get("location", ""))
            if not url.startswith("https://"):
                return {"result": "error", "error": "Polytope redirected to a non-https URL"}
            status, hdrs, body = http("GET", url, key_for(url))
        if status != 200:
            msg = _message(body)
            return {"result": _classify(status, msg), "error": f"HTTP {status}: {msg}"}
        try:
            parse_covjson(json.loads(body))
        except (ValueError, KeyError, IndexError, TypeError):
            return {"result": "error", "error": "Polytope answered with unexpected content"}
        return {"result": "ok", "bytes": len(body)}
    except (OSError, TimeoutError) as e:  # URLError, connection refused, socket timeout
        return {"result": "network", "error": str(e)[:300]}
    finally:
        if job:
            with contextlib.suppress(Exception):  # best effort: the job expires server-side
                http("DELETE", job, key_for(job))


def test_request(address: str, auth: str, http=_http, sleep=time.sleep, runs=None) -> dict:
    """One tiny real extraction: the only proof the account may read operational data (the
    server lists every collection to everyone and checks permission on submit). A run not yet
    released is retried once with the previous run."""
    runs = runs or candidate_runs()[:2]
    t0 = time.monotonic()
    for run in runs[:2]:
        r = _one_test_request(address, auth, run, http, sleep)
        r["run"] = f"{run[0]} {run[1]}"
        if r["result"] != "no_data":
            break
    r["seconds"] = round(time.monotonic() - t0, 1)
    return r


def check(as_json: bool, offline: bool = False, http=_http, sleep=time.sleep) -> int:
    """Credentials, the account's collections and (unless offline) one tiny real request.
    Exit 0 usable, 4 blocked, 2 not verified (no data yet, service trouble)."""
    src = credentials()
    res = {
        "polytope": bool(src),
        "credentials": src,
        "address": ADDRESS,
        "fallback": "ecmwf-open-data skill: uv run <ecmwf-open-data>/scripts/odpoint.py "
        "--lat LAT --lon LON",
    }
    code = 0 if src else 4
    if src:
        try:
            auth = auth_header()
        except MalformedCredentials as e:
            return blocked(malformed_barrier(e), as_json)
        try:
            cols = list_collections(ADDRESS, auth)
            res.update(verified=True, collections=cols, point_forecasts=COLLECTION in cols)
            res["verified_by"] = "collection_list"
            if COLLECTION not in cols:
                res["polytope"], code = False, 4
                res["offer"] = (
                    f"Polytope works but '{COLLECTION}' (operational forecasts) is not "
                    "among this account's collections; use ecmwf-open-data and offer "
                    "the access steps (ptpoint.py --setup)."
                )
            elif not offline:
                code = _apply_test_request(res, test_request(ADDRESS, auth, http, sleep))
        except AccessDenied as e:
            res.update(polytope=False, verified=False, error=str(e))
            code = 4
        except Exception as e:
            b = barrier_for_error(str(e))
            res.update(polytope=False, verified=None, error=str(e).splitlines()[-1][:300])
            code = 4
            if b:
                res["status"] = b.get("status")
    if not res["polytope"] and "offer" not in res:
        res["offer"] = (
            "Say once what Polytope would add (hourly point data in KB) and offer "
            "step-by-step setup instructions (ptpoint.py --setup); give them only if "
            "accepted. Destination Earth data: the ecmwf-destine skill."
        )
    if as_json:
        print(json.dumps(res, indent=2))
    else:
        print("\n".join(f"{k}: {v}" for k, v in res.items() if k != "blocked"))
    return code


def _apply_test_request(res: dict, r: dict) -> int:
    """Record the test request's outcome in the check result; returns the exit code."""
    res.update(test_run=r["run"], seconds=r["seconds"])
    if r["result"] == "ok":
        res.update(point_forecasts=True, verified_by="test_request")
        return 0
    res.update(polytope=False, error=r["error"])
    if r["result"] == "no_data":
        res.update(
            verified=None,
            point_forecasts=None,
            offer=(
                "No recent run answered yet (not released); Polytope access is unconfirmed. "
                "Use ecmwf-open-data meanwhile and re-run the check later."
            ),
        )
        return 2
    b = {
        "entitlement": entitlement_barrier,
        "key": lambda: key_rejected_barrier(r["error"][:40]),
        "network": lambda: ecmwf_status.network_barrier("polytope", r["error"]),
    }.get(r["result"])
    if b is None:  # an unexpected answer: report it, don't guess a barrier
        res.update(verified=None, point_forecasts=None)
        return 2
    res["blocked"] = b()
    res["point_forecasts"] = False if r["result"] != "network" else None
    if r["result"] == "key":
        res["verified"] = False
    blocked(res["blocked"])  # the report on stderr; stdout keeps one document
    return 2 if r["result"] == "network" else 4


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--check", action="store_true", help="report access with a tiny test request (no keys)"
    )
    ap.add_argument(
        "--offline",
        action="store_true",
        help="with --check: only list the account's collections (no data request)",
    )
    ap.add_argument("--setup", action="store_true", help="how to obtain Polytope credentials")
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--steps", default="0-240", help="0-END hours, e.g. 0-24, 0-120")
    ap.add_argument("--ensemble", action="store_true", help="50 ENS members -> p10/p50/p90")
    ap.add_argument("--date", help="YYYYMMDD (default: latest available run)")
    ap.add_argument("--time", help="0000 or 1200")
    ap.add_argument(
        "--tz", help="IANA time zone for local_time and daily grouping, e.g. Europe/London"
    )
    ap.add_argument("--from-now", action="store_true", help="drop rows already in the past")
    ap.add_argument(
        "--next-hours", type=int, help="exactly the next N hours from now (overrides --steps)"
    )
    ap.add_argument(
        "--daily",
        action="store_true",
        help="add per-day high/low/precipitation (ensemble: p10/p50/p90)",
    )
    ap.add_argument(
        "--indices",
        action="store_true",
        help="add feels-like columns (heat index, humidex, apparent temperature, wind chill; "
        "thermofeel; ensemble: p10/p50/p90); requests 2 m dewpoint too",
    )
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.setup:
        print("\n".join(setup_steps()))
        return 0
    if a.check:
        return check(a.json, offline=a.offline)
    if a.lat is None or a.lon is None:
        ap.error("--lat and --lon are required (or use --check)")
    if not credentials():
        return blocked(
            {
                "blocked": "no Polytope credentials on this machine",
                "why": "Polytope needs an ECMWF key and an account with Polytope access.",
                "user_steps": setup_steps(),
                "agent": "Answer now with the ecmwf-open-data skill "
                "(uv run <ecmwf-open-data>/scripts/odpoint.py --lat LAT --lon LON) and "
                "offer these steps once.",
            },
            a.json,
        )
    # Confirm the account can use the collection before requesting data (fast, authenticated).
    try:
        cols = list_collections(ADDRESS, auth_header())
    except MalformedCredentials as e:
        return blocked(malformed_barrier(e), a.json)
    except AccessDenied as e:
        return blocked(barrier_for_error(str(e)), a.json)
    except Exception as e:
        b = barrier_for_error(str(e))
        if b:
            return blocked(b, a.json)
        cols = None  # endpoint unavailable: let the data request report the problem
    if cols is not None and COLLECTION not in cols:
        return blocked(collection_barrier(COLLECTION, cols), a.json)
    try:
        end = int(a.steps.split("-")[-1])
        if a.next_hours:
            end = steps_for_next_hours(a.next_hours)
        d, nbytes = fetch_latest(a.lat, a.lon, end, a.ensemble, a.date, a.time, a.indices)
        p = parse_covjson(d)
        rows = series(p)
    except ImportError as e:
        print(f"error: {e}. Run with `uv run ptpoint.py …`.", file=sys.stderr)
        return 3
    except RuntimeError as e:
        b = barrier_for_error(str(e))
        if b:
            return blocked(b, a.json)
        print(f"error: {e}", file=sys.stderr)
        return 2

    units = {k: v for k, v in UNITS.items() if any(k in r for r in rows)}
    res = {
        "source": "polytope",
        "model": "ifs-ens" if a.ensemble else "ifs",
        "location": {"lat": a.lat, "lon": a.lon},
        "gridpoint": p["gridpoint"],
        "run": p["run"],
        "units": units,
        **shape_output(rows, p, a.tz, a.from_now, a.daily, next_hours=a.next_hours),
        "note": size_note(nbytes),
        "attribution": attribution(d),
    }
    if a.indices:
        res["indices_note"] = INDICES_NOTE
    if a.json:
        print(json.dumps(res, indent=2))
        return 0
    rows = res["series"]
    gp = p["gridpoint"]
    model = f"IFS ENS ({rows[0].get('members')} members)" if a.ensemble else "IFS HRES"
    print(f"ECMWF {model} run {p['run']} — gridpoint {gp['lat']}, {gp['lon']}")
    cols = [c for c in rows[0] if c not in ("step", "valid_time", "members")]
    print("valid_time         " + "  ".join(f"{c:>12}" for c in cols))
    for r in rows:
        cells = ("" if r.get(c) is None else r[c] for c in cols)
        print(f"{r['valid_time']:<18} " + "  ".join(f"{v:>12}" for v in cells))
    attr = res["attribution"]
    if a.indices:
        print(f"Indices: {INDICES_NOTE}")
    print(f"Note: {res['note']}\nAttribution: {attr['short']} — {attr['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
