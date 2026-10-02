#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "earthkit-data[polytope,covjsonkit]>=1.2",
#   "earthkit-meteo>=1.2",
#   "earthkit-utils>=1.0",
# ]
# ///
"""Point forecast via ECMWF Polytope — server-side extraction, a few KB instead of global fields.

earthkit components, each for its job:
  earthkit-data[polytope]  submit the feature-extraction request (polytope-client)
  earthkit-meteo           wind speed / direction
  earthkit-utils           unit conversion

  uv run ptpoint.py --check                                  # credentials + test request
  uv run ptpoint.py --lat 38.72 --lon -9.14                  # IFS HRES, hourly, 0-240 h
  uv run ptpoint.py --lat 38.72 --lon -9.14 --steps 0-24 --json
  uv run ptpoint.py --lat 51.45 --lon -0.97 --ensemble --steps 0-120   # 50-member percentiles

Output JSON matches the open-data skill's odpoint.py (plus *_p10/_p50/_p90 for --ensemble), so
the earthkit skill's `ekplot.py meteogram` plots it. Exit code 4 = no Polytope credentials:
use the open-data skill instead.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
ADDRESS = os.environ.get("POLYTOPE_ADDRESS", "polytope.ecmwf.int")
COLLECTION = "ecmwf-mars"
# paramIds: 2t, tp, 10u, 10v, msl, tcc (ensemble: 2t, tp — enough for uncertainty, keeps it small)
PARAMS = "167/228/165/166/151/164"
ENS_PARAMS = "167/228"
# Operational runs are usable ~7 h after base time; Polytope keeps roughly the last 2 days.
AVAILABLE_AFTER = timedelta(hours=7)
MAX_CANDIDATES = 4
QUANTILES = (10, 50, 90)
UNITS = {
    "t2m_C": "degC",
    "precip_mm": "mm since previous step",
    "wind_speed_ms": "m/s",
    "wind_dir_deg": "degrees (from, meteorological)",
    "msl_hPa": "hPa",
    "tcc_pct": "%",
}


# --- credentials ------------------------------------------------------------------------------


def credentials(env=os.environ, home: Path | None = None) -> str | None:
    """Where polytope-client will find a key (name only — never the value)."""
    home = home or Path.home()
    if env.get("POLYTOPE_USER_KEY"):
        return "POLYTOPE_USER_KEY"
    if (home / ".polytopeapirc").exists():
        return "~/.polytopeapirc"
    if (home / ".ecmwfapirc").exists():
        return "~/.ecmwfapirc"
    return None


# --- request ------------------------------------------------------------------------------------


def build_request(
    lat: float,
    lon: float,
    date: str,
    time: str,
    end_step: int = 240,
    ensemble: bool = False,
    members: str = "1/to/50",
) -> dict:
    r = {
        "class": "od",
        "stream": "enfo" if ensemble else "oper",
        "type": "pf" if ensemble else "fc",
        "date": date,
        "time": time,
        "levtype": "sfc",
        "expver": "0001",
        "domain": "g",
        "param": ENS_PARAMS if ensemble else PARAMS,
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


def candidate_runs(now: datetime | None = None) -> list[tuple[str, str]]:
    now = (now or datetime.now(UTC)) - AVAILABLE_AFTER
    t = now.replace(minute=0, second=0, microsecond=0, hour=0 if now.hour < 12 else 12)
    out = []
    for _ in range(MAX_CANDIDATES):
        out.append((t.strftime("%Y%m%d"), t.strftime("%H00")))
        t -= timedelta(hours=12)
    return out


# --- parsing --------------------------------------------------------------------------------------


def _norm_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


def parse_covjson(d: dict) -> dict:
    covs = d["coverages"]
    axes = covs[0]["domain"]["axes"]
    run = datetime.strptime(
        covs[0]["mars:metadata"]["Forecast date"], "%Y-%m-%dT%H:%M:%SZ"
    )
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
            int(
                (datetime.strptime(t, "%Y-%m-%dT%H:%M:%SZ") - run).total_seconds()
                // 3600
            )
            for t in times
        ],
        "members": members,
        "metadata": covs[0]["mars:metadata"],
    }


def _percentile(vals: list[float], q: float) -> float:
    s = sorted(vals)
    k = (len(s) - 1) * q / 100.0
    lo = int(k)
    hi = min(lo + 1, len(s) - 1)
    return round(s[lo] + (s[hi] - s[lo]) * (k - lo), 4)


def quantiles(member_series: list[list[float]], qs=QUANTILES) -> list[list[float]]:
    """members x times -> times x quantiles."""
    return [
        [_percentile([m[i] for m in member_series], q) for q in qs]
        for i in range(len(member_series[0]))
    ]


def _deaccumulate_mm(vals: list[float]) -> list[float]:
    out, prev = [], 0.0
    for v in vals:
        out.append(round(max(0.0, (v - prev) * 1000.0), 2))
        prev = v
    return out


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


# --- earthkit-backed ------------------------------------------------------------------------------


def series(p: dict) -> list[dict]:
    import numpy as np
    from earthkit.meteo import wind
    from earthkit.utils.units import convert_units

    rows = [{"step": s, "valid_time": t} for s, t in zip(p["steps"], p["times"])]
    mem = p["members"]
    if list(mem) == [0]:  # deterministic
        m = mem[0]
        if "2t" in m:
            for r, v in zip(
                rows, convert_units(np.array(m["2t"]), "degC", source_units="K")
            ):
                r["t2m_C"] = round(float(v), 1)
        if "tp" in m:
            for r, v in zip(rows, _deaccumulate_mm(m["tp"])):
                r["precip_mm"] = v
        if "10u" in m and "10v" in m:
            u, v = np.array(m["10u"]), np.array(m["10v"])
            for r, s, dd in zip(
                rows, wind.speed(u, v), wind.direction(u, v, convention="meteo")
            ):
                r["wind_speed_ms"], r["wind_dir_deg"] = (
                    round(float(s), 1),
                    round(float(dd), 1),
                )
        if "msl" in m:
            for r, v in zip(
                rows, convert_units(np.array(m["msl"]), "hPa", source_units="Pa")
            ):
                r["msl_hPa"] = round(float(v), 1)
        if "tcc" in m:
            for r, v in zip(rows, m["tcc"]):
                r["tcc_pct"] = round(float(v) * 100.0, 1)
        return rows

    names = sorted(mem)
    t2 = [
        list(convert_units(np.array(mem[n]["2t"]), "degC", source_units="K"))
        for n in names
    ]
    tp = (
        [_deaccumulate_mm(mem[n]["tp"]) for n in names]
        if "tp" in mem[names[0]]
        else None
    )
    for i, (r, q) in enumerate(zip(rows, quantiles(t2))):
        r.update({f"t2m_C_p{k}": round(v, 1) for k, v in zip(QUANTILES, q)})
        r["t2m_C"] = r["t2m_C_p50"]
        r["members"] = len(names)
    if tp:
        for r, q in zip(rows, quantiles(tp)):
            r.update({f"precip_mm_p{k}": round(v, 2) for k, v in zip(QUANTILES, q)})
            r["precip_mm"] = r["precip_mm_p50"]
    return rows


def fetch(req: dict) -> tuple[dict, int]:
    import earthkit.data as ekd

    with contextlib.redirect_stdout(sys.stderr):
        ds = ekd.from_source("polytope", COLLECTION, req, stream=False, address=ADDRESS)
    raw = Path(ds.path).read_bytes()
    return json.loads(raw), len(raw)


def fetch_latest(
    lat, lon, end_step, ensemble, date=None, time=None
) -> tuple[dict, int]:
    runs = [(date, time)] if date else candidate_runs()
    last = None
    for d, t in runs:
        try:
            return fetch(build_request(lat, lon, d, t, end_step, ensemble))
        except (
            Exception
        ) as e:  # polytope-client raises generic errors; retry older runs on "no data"
            msg = str(e)
            if any(k in msg for k in ("401", "403", "Unauthorized", "Forbidden")):
                raise RuntimeError(
                    "Polytope rejected the credentials (no access for this account) — "
                    "use the open-data skill instead"
                ) from e
            last = msg.splitlines()[-1] if msg else repr(e)
    raise RuntimeError(f"no run found among {runs}: {last}")


# --- CLI ------------------------------------------------------------------------------------------


def check(as_json: bool) -> int:
    src = credentials()
    res = {
        "polytope": bool(src),
        "credentials": src,
        "address": ADDRESS,
        "fallback": "open-data skill: uv run <open-data>/scripts/odpoint.py --lat LAT --lon LON",
    }
    if src:
        try:
            _, n = fetch_latest(51.5, 0.0, 0, False)
            res.update(verified=True, test_bytes=n)
        except ImportError:
            res.update(
                verified=None,
                hint="run with `uv run ptpoint.py --check` to test access",
            )
        except Exception as e:
            res.update(
                polytope=False, verified=False, error=str(e).splitlines()[-1][:300]
            )
    print(
        json.dumps(res, indent=2)
        if as_json
        else "\n".join(f"{k}: {v}" for k, v in res.items())
    )
    return 0 if res["polytope"] else 4


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--check", action="store_true", help="report access (never prints keys)"
    )
    ap.add_argument("--lat", type=float)
    ap.add_argument("--lon", type=float)
    ap.add_argument("--steps", default="0-240", help="0-END hours, e.g. 0-24, 0-120")
    ap.add_argument(
        "--ensemble", action="store_true", help="50 ENS members -> p10/p50/p90"
    )
    ap.add_argument("--date", help="YYYYMMDD (default: latest available run)")
    ap.add_argument("--time", help="0000 or 1200")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    if a.check:
        return check(a.json)
    if a.lat is None or a.lon is None:
        ap.error("--lat and --lon are required (or use --check)")
    if not credentials():
        print(
            "error: no Polytope credentials (POLYTOPE_USER_KEY, ~/.polytopeapirc or ~/.ecmwfapirc). "
            "Use the open-data skill: uv run <open-data>/scripts/odpoint.py --lat LAT --lon LON",
            file=sys.stderr,
        )
        return 4
    try:
        end = int(a.steps.split("-")[-1])
        d, nbytes = fetch_latest(a.lat, a.lon, end, a.ensemble, a.date, a.time)
        p = parse_covjson(d)
        rows = series(p)
    except ImportError as e:
        print(f"error: {e}. Run with `uv run ptpoint.py …`.", file=sys.stderr)
        return 3
    except RuntimeError as e:
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
        "series": rows,
        "note": size_note(nbytes),
        "attribution": attribution(d),
    }
    if a.json:
        print(json.dumps(res, indent=2))
        return 0
    gp = p["gridpoint"]
    print(
        f"ECMWF {'IFS ENS (' + str(rows[0].get('members')) + ' members)' if a.ensemble else 'IFS HRES'} "
        f"run {p['run']} — gridpoint {gp['lat']}, {gp['lon']}"
    )
    cols = [c for c in rows[0] if c not in ("step", "valid_time", "members")]
    print("valid_time         " + "  ".join(f"{c:>12}" for c in cols))
    for r in rows:
        print(
            f"{r['valid_time']:<18} " + "  ".join(f"{r.get(c, ''):>12}" for c in cols)
        )
    print(
        f"Note: {res['note']}\nAttribution: {res['attribution']['short']} — {res['attribution']['note']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
