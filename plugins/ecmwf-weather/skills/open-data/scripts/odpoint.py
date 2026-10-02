#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "earthkit-data[ecmwf-opendata]>=1.2",
#   "earthkit-geo>=1.1",
#   "earthkit-meteo>=1.2",
#   "earthkit-utils>=1.0",
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
With ECMWF Polytope access the same request is a few KB (see the polytope skill).
"""

from __future__ import annotations

import argparse
import atexit
import contextlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import odcatalog as oc  # noqa: E402  (stdlib sibling: latest run, sizes, attribution)

UTC = timezone.utc
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


def deaccumulate(values_m: list[float], steps: list[int] | None = None) -> list[float | None]:
    """Accumulated precipitation (m, since step 0) -> mm per interval since the previous step.

    If the series does not start at step 0 the first interval is unknown (its value covers the
    whole run so far), so it is returned as None rather than a misleading total.
    """
    out: list[float | None] = []
    prev = 0.0
    for k, v in enumerate(values_m):
        if k == 0 and steps is not None and steps[0] != 0:
            out.append(None)
        else:
            out.append(round(max(0.0, (v - prev) * 1000.0), 2))
        prev = v
    return out


def normalise_lon(lon: float) -> float:
    return ((lon + 180.0) % 360.0) - 180.0


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
            "were found — if your account has Polytope access (member-state users), the "
            "polytope skill can extract this point server-side in a few KB."
        )
    return (
        f"Answered from free ECMWF Open Data ({size} of global fields downloaded for one point). "
        "With ECMWF Polytope access (member-state users or Destination Earth accounts) this "
        "would be a single point request of a few KB."
    )


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
    fmt = p.add_mutually_exclusive_group()
    fmt.add_argument("--json", action="store_true")
    fmt.add_argument("--csv", action="store_true")
    return p


def main(argv=None) -> int:
    p = build_parser()
    a = p.parse_args(argv)
    params = a.params.split(",")

    try:
        nbytes = 0
        if a.file:
            import earthkit.data as ekd

            fl = ekd.from_source("file", a.file).to_fieldlist()
            nbytes = os.path.getsize(a.file)
        else:
            if a.date:
                run = datetime.strptime(a.date, "%Y%m%d").replace(
                    hour=a.time or 0, tzinfo=UTC
                )
            else:
                run = oc.latest_run(
                    a.model, "oper", step=oc.max_step(a.steps), source=a.source
                )
                if run is None:
                    raise RuntimeError("no published run found in the last 72 h")
            steps = oc._parse_steps(
                a.steps, oc.steps_for(a.model, "oper", run.hour, run.date())
            )
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

                    tmp = tempfile.NamedTemporaryFile(suffix=".grib2", delete=False)
                    tmp.close()
                    atexit.register(os.unlink, tmp.name)
                    oc.download_ranges(oc.range_jobs(per_step), tmp.name, workers=a.workers)
                    fl = ekd.from_source("file", tmp.name).to_fieldlist()
                len(fl)  # force download/decode inside the redirect
    except ImportError as e:
        print(
            f"error: earthkit not available ({e}). Run with `uv run odpoint.py ...`, or "
            "pip install 'earthkit-data[ecmwf-opendata]>=1.2' 'earthkit-geo>=1.1' "
            "'earthkit-meteo>=1.2' 'earthkit-utils>=1.0'. Without earthkit, odcatalog.py can "
            "still download the GRIB fields but cannot decode them.",
            file=sys.stderr,
        )
        return 3
    except (ValueError, RuntimeError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    res = point_series(fl, a.lat, a.lon)
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
            print(
                f"{r['valid_time']:<18} "
                + "  ".join(f"{r.get(c, ''):>13}" for c in cols)
            )
        print(f"Note: {res['note']}")
        print(f"Attribution: {res['attribution']['short']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
