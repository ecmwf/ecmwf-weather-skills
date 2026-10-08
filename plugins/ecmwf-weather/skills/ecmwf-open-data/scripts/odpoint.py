#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "earthkit-data[ecmwf-opendata]>=1.2",
#   "earthkit-geo>=1.1",
#   "earthkit-meteo>=1.2",
#   "earthkit-utils>=1.0",
#   "earthkit-transforms[all]>=1.0",
# ]
# ///
"""Point forecast from ECMWF Open Data — nearest gridpoint time series.

earthkit components, each for its job:
  earthkit-data   fetch (ecmwf-open-data source) and decode GRIB
  earthkit-geo    nearest gridpoint (KD-tree on the sphere)
  earthkit-meteo  wind speed / direction from 10u/10v
  earthkit-utils  unit conversion (K -> degC, Pa -> hPa)

Run with uv (dependencies install on first run, ~150 MB, cached):
  uv run odpoint.py --lat 38.72 --lon -9.14                 # Lisbon, latest IFS, 0-240 h
  uv run odpoint.py --lat 51.5 --lon -0.12 --steps 0-48 --csv
  uv run odpoint.py --lat 40 --lon -10 --file local.grib2    # decode an existing file
  uv run odpoint.py --lat 38.72 --lon -9.14 --estimate-only  # size only, no decoding

Open Data has no point API: whole global fields are downloaded and one point is extracted.
With ECMWF Polytope access the same request is a few KB (see the ecmwf-polytope skill).
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import importlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # heavy; imported inside the functions that need it
    import xarray as xr


# Never write __pycache__ into the installed skill (it may be read-only, and skills must
# not be modified); sibling scripts are imported below.
sys.dont_write_bytecode = True
oc = importlib.import_module("odcatalog")  # stdlib sibling script (latest run, sizes, attribution)

UTC = timezone.utc
# A run is published ~7 h after base time and replaced 6 h later (up to ~13 h old).
MAX_RUN_AGE_H = 14
DEFAULT_PARAMS = ["2t", "tp", "10u", "10v", "msl", "tcc"]
UNITS = {
    "t2m_C": "degC",
    "precip_mm": "mm since previous step",
    "wind_speed_ms": "m/s",
    "wind_dir_deg": "degrees (from, meteorological)",
    "msl_hPa": "hPa",
    "tcc_pct": "%",
}


# --- pure helpers -------------------------------------------------------------------------------


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


def deaccumulate(values_m: list[float], steps: list[int] | None = None) -> list[float | None]:
    return deaccumulate_mm(values_m, steps)


def daily_from_rows(rows: list[dict], zone) -> list[dict]:
    """Per local day high/low (°C) and precipitation (mm) with earthkit-transforms."""
    import numpy as np
    import xarray as xr
    from earthkit.transforms import temporal

    local = [_utc(r["valid_time"]).astimezone(zone) for r in rows]
    axis = np.array([t.replace(tzinfo=None) for t in local], dtype="datetime64[ns]")

    def series(key):
        vals = [np.nan if r.get(key) is None else r[key] for r in rows]
        return xr.DataArray(
            np.asarray(vals, float), dims=["valid_time"], coords={"valid_time": axis}
        )

    stats = {}
    if any(r.get("t2m_C") is not None for r in rows):
        t = series("t2m_C")
        stats["high_C"] = temporal.daily_max(t, time_dim="valid_time")
        stats["low_C"] = temporal.daily_min(t, time_dim="valid_time")
    if any(r.get("precip_mm") is not None for r in rows):
        stats["precip_mm"] = temporal.daily_sum(
            series("precip_mm").fillna(0.0), time_dim="valid_time"
        )
    out = []
    for d in sorted({t.date() for t in local}):
        on_day = [t for t in local if t.date() == d]
        day = {
            "date": d.isoformat(),
            "partial": (on_day[-1] - on_day[0]).total_seconds() / 3600 < 18,
        }
        key = np.datetime64(d.isoformat(), "ns")
        for name, da in stats.items():
            if key in da["valid_time"].values:
                day[name] = round(float(da.sel(valid_time=key)), 1)
        out.append(day)
    return out


def normalise_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def earthkit_barrier(message: str) -> dict:
    b = {
        "blocked": "the earthkit components this script needs are not installed",
        "why": "decoding GRIB needs earthkit (eccodes wheels for Linux/macOS).",
        "user_steps": [
            "1. Install uv: https://docs.astral.sh/uv/getting-started/installation/ "
            "(curl -LsSf https://astral.sh/uv/install.sh | sh), then re-run with "
            "`uv run odpoint.py …` — it installs exactly what is needed.",
            "2. Without uv: pip install 'earthkit-data[ecmwf-opendata]>=1.2' "
            "'earthkit-geo>=1.1' 'earthkit-meteo>=1.2' 'earthkit-utils>=1.0'",
            "3. On Windows use WSL (earthkit has no Windows wheels).",
        ],
        "agent": "Run the script with `uv run`; if uv cannot be installed, give these "
        "steps. Without earthkit, `odcatalog.py download` still fetches the "
        "exact GRIB fields (undecoded).",
    }
    b["detail"] = message
    return b


def polytope_available(env=os.environ, home: Path | None = None) -> bool:
    home = home or Path.home()
    return (
        bool(env.get("POLYTOPE_USER_KEY"))
        or (home / ".polytopeapirc").exists()
        or (home / ".ecmwfapirc").exists()
    )


def access_note(nbytes: int, env=os.environ, home: Path | None = None) -> str:
    size = oc.human_size(nbytes)
    if polytope_available(env, home):
        return (
            f"Downloaded {size} of global Open Data fields for one point. ECMWF API credentials "
            "were found — Polytope is available to authenticated ECMWF users, and the "
            "ecmwf-polytope skill can extract this point server-side in a few KB."
        )
    return (
        f"Answered from free ECMWF Open Data ({size} of global fields downloaded for one point). "
        "With an ECMWF account (Polytope, same key as the Web API) this would be a single "
        "point request "
        "of a few KB — offer instructions to obtain it (ecmwf-polytope skill: ptpoint.py --setup)."
    )


# --- local time and daily summaries (standard library) -------------------------------------------


def _utc(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)


def shape_output(
    rows: list[dict],
    tz: str | None = None,
    from_now: bool = False,
    daily: bool = False,
    now: datetime | None = None,
    next_hours: int | None = None,
) -> dict:
    """Optional local times, dropping past rows and per-local-day high/low/precipitation.

    A precipitation interval counts for the day it ends in; days whose samples span less
    than 18 h are marked partial. Daily values use all rows, including past hours of today.
    """
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(tz or "UTC")
    rows = [dict(r) for r in rows]  # never modify the caller's rows
    out: dict = {}
    if tz:
        for r in rows:
            r["local_time"] = _utc(r["valid_time"]).astimezone(zone).isoformat(timespec="minutes")
        out["timezone"] = tz
    if daily:
        out["daily"] = daily_from_rows(rows, zone)
    now = now or datetime.now(UTC)
    end = now + timedelta(hours=next_hours) if next_hours else None
    if from_now or next_hours:
        rows = [r for r in rows if _utc(r["valid_time"]) >= now]
    out["series"] = [r for r in rows if end is None or _utc(r["valid_time"]) < end]
    return out


# --- earthkit-backed ------------------------------------------------------------------------------


def point_series(fl, lat: float, lon: float) -> dict:
    """Nearest-gridpoint series from a FieldList with any of DEFAULT_PARAMS."""
    import numpy as np
    from earthkit.geo import distance
    from earthkit.meteo import wind
    from earthkit.utils.units import convert_units

    lats, lons = fl[0].geography.latlons()
    idx, dist = distance.nearest_point_kdtree((lat, lon), (lats, lons))
    i = int(np.asarray(idx).ravel()[0])
    glat, glon = float(lats.ravel()[i]), normalise_lon(float(lons.ravel()[i]))

    raw: dict[str, dict[int, float]] = {}
    valid: dict[int, datetime] = {}
    base = None
    for f in fl:
        name = f.metadata("shortName")
        step = int(f.time.step().total_seconds() // 3600)
        raw.setdefault(name, {})[step] = float(f.to_numpy(flatten=True)[i])
        valid[step] = f.time.valid_datetime()
        base = base or f.time.base_datetime()

    steps = sorted(valid)

    def col(p):
        return np.array([raw[p][s] for s in steps]) if p in raw else None

    t2m, tp, u, v, msl, tcc = (col(p) for p in DEFAULT_PARAMS)
    derived = {}
    if t2m is not None:
        derived["t2m_C"] = convert_units(t2m, "degC", source_units="K")
    if tp is not None:
        derived["precip_mm"] = deaccumulate(list(tp), steps)
    if u is not None and v is not None:
        derived["wind_speed_ms"] = wind.speed(u, v)
        derived["wind_dir_deg"] = wind.direction(u, v, convention="meteo")
    if msl is not None:
        derived["msl_hPa"] = convert_units(msl, "hPa", source_units="Pa")
    if tcc is not None:
        derived["tcc_pct"] = tcc * 100.0

    rows = []
    for k, s in enumerate(steps):
        row = {"step": s, "valid_time": valid[s].strftime("%Y-%m-%dT%H:%MZ")}
        for key, vals in derived.items():
            row[key] = None if vals[k] is None else round(float(vals[k]), 1)
        rows.append(row)

    return {
        "location": {"lat": lat, "lon": lon},
        "gridpoint": {
            "lat": glat,
            "lon": glon,
            "distance_km": round(float(np.asarray(dist).ravel()[0]) / 1000.0, 1),
        },
        "run": base.strftime("%Y-%m-%dT%H:%MZ") if base else None,
        "units": {k: UNITS[k] for k in derived},
        "series": rows,
    }


def fetch(model: str, params: list[str], steps: list[int], run: datetime, source: str):
    import earthkit.data as ekd

    return ekd.from_source(
        "ecmwf-open-data",
        source=source,
        model=model,
        param=params,
        levtype="sfc",
        step=steps,
        date=run.strftime("%Y%m%d"),
        time=run.hour,
    ).to_fieldlist()


# --- CLI ------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--lat", type=float, required=True)
    p.add_argument("--lon", type=float, required=True)
    p.add_argument("--model", default="ifs", choices=["ifs", "aifs-single"])
    p.add_argument("--steps", default="0-240", help="e.g. 0-240 | 0-48 | 0,24,48")
    p.add_argument("--params", default=",".join(DEFAULT_PARAMS))
    p.add_argument("--source", default="ecmwf", choices=list(oc.ROOTS))
    p.add_argument("--date", help="YYYYMMDD (default: latest run covering --steps)")
    p.add_argument("--time", type=int, help="run hour")
    p.add_argument("--file", help="decode an existing GRIB file instead of downloading")
    p.add_argument("--estimate-only", action="store_true")
    p.add_argument(
        "--fetcher",
        default="parallel",
        choices=["parallel", "earthkit"],
        help="parallel: HTTP Range requests over --workers connections (~5x faster), decoded "
        "with earthkit-data; earthkit: earthkit-data's ecmwf-open-data source (sequential)",
    )
    p.add_argument("--workers", type=int, default=oc.DEFAULT_WORKERS)
    p.add_argument(
        "--tz", help="IANA time zone for local_time and daily grouping, e.g. Europe/Lisbon"
    )
    p.add_argument("--from-now", action="store_true", help="drop rows already in the past")
    p.add_argument(
        "--next-hours", type=int, help="exactly the next N hours from now (overrides --steps)"
    )
    p.add_argument("--daily", action="store_true", help="add per-day high/low/precipitation")
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--csv", action="store_true")
    return p


def main(argv=None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    params = a.params.split(",")
    if a.next_hours:  # newest run can be ~13 h old: fetch enough lead time, trim afterwards
        a.steps = f"0-{-(-(a.next_hours + MAX_RUN_AGE_H) // 6) * 6}"

    try:
        nbytes = 0
        if a.file:
            import earthkit.data as ekd

            fl = ekd.from_source("file", a.file).to_fieldlist()
            nbytes = os.path.getsize(a.file)
        else:
            if a.date:
                run = datetime.strptime(a.date, "%Y%m%d").replace(hour=a.time or 0, tzinfo=UTC)
            else:
                run = oc.latest_run(a.model, "oper", step=oc.max_step(a.steps), source=a.source)
                if run is None:
                    raise RuntimeError("no published run found in the last 72 h")
            steps = oc._parse_steps(a.steps, oc.steps_for(a.model, "oper", run.hour, run.date()))
            per_step = []
            for s in steps:
                idx_url = oc.file_url(run, a.model, "oper", s, source=a.source, ext="index")
                sel = oc.select(oc.parse_index(oc.fetch_text(idx_url)), params, "sfc")
                per_step.append((s, idx_url, sel))
                nbytes += oc.estimate_bytes(sel)
            if a.estimate_only:
                print(
                    json.dumps(
                        {
                            "run": run.strftime("%Y-%m-%dT%H:%MZ"),
                            "steps": len(steps),
                            "bytes": nbytes,
                            "size": oc.human_size(nbytes),
                            "note": access_note(nbytes),
                        },
                        indent=2,
                    )
                )
                return 0
            print(
                f"Downloading ~{oc.human_size(nbytes)} from ECMWF Open Data "
                f"({a.model} {run:%Y-%m-%d %H}z)...",
                file=sys.stderr,
            )
            # Library banners (licence notice, connection limits) go to stdout; keep stdout clean
            # for --json/--csv consumers.
            with contextlib.redirect_stdout(sys.stderr):
                if a.fetcher == "earthkit":
                    fl = fetch(a.model, params, steps, run, a.source)
                else:
                    import earthkit.data as ekd

                    fd, tmp_path = tempfile.mkstemp(suffix=".grib2")
                    os.close(fd)
                    atexit.register(os.unlink, tmp_path)
                    oc.download_ranges(oc.range_jobs(per_step), tmp_path, workers=a.workers)
                    fl = ekd.from_source("file", tmp_path).to_fieldlist()
                len(fl)  # force download/decode inside the redirect
    except ImportError as e:
        return oc.blocked(earthkit_barrier(str(e)), a.json, code=3)
    except RuntimeError as e:
        if "no published run" in str(e):
            return oc.blocked(oc.no_run_barrier(a.source), a.json, code=2)
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    res = point_series(fl, a.lat, a.lon)
    res.update(shape_output(res["series"], a.tz, a.from_now, a.daily, next_hours=a.next_hours))
    res["model"] = a.model
    res["note"] = access_note(nbytes)
    if res["run"]:
        res["freshness"] = oc.freshness(
            datetime.strptime(res["run"], "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC),
            a.model,
        )
    res["attribution"] = oc.attribution()

    if a.json:
        print(json.dumps(res, indent=2))
    elif a.csv:
        cols = ["valid_time", "step", *res["units"]]
        print(",".join(cols))
        for r in res["series"]:
            print(",".join(str(r.get(c, "")) for c in cols))
    else:
        gp = res["gridpoint"]
        print(
            f"ECMWF {a.model.upper()} run {res['run']} — gridpoint {gp['lat']}, {gp['lon']} "
            f"({gp['distance_km']} km from requested point)"
        )
        cols = list(res["units"])
        print("valid_time         " + "  ".join(f"{c:>13}" for c in cols))
        for r in res["series"]:
            print(f"{r['valid_time']:<18} " + "  ".join(f"{r.get(c, ''):>13}" for c in cols))
        print(f"Note: {res['note']}")
        print(f"Attribution: {res['attribution']['short']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
