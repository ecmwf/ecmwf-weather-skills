#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["earthkit-plots>=1.0", "earthkit-data>=1.2"]
# ///
"""Plot ECMWF data with earthkit-plots.

  map        one field from a GRIB/NetCDF file on a map
  meteogram  multi-panel time series from point-forecast JSON (open-data skill's odpoint.py --json)

  uv run ekplot.py map forecast.grib2 --param 2t --step 24 --units celsius --domain Europe -o t2m.png
  uv run ekplot.py meteogram lisbon.json -o lisbon.png

Every figure carries the ECMWF CC-BY-4.0 attribution line.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from datetime import datetime, timezone

PANELS = [  # key in odpoint JSON, title, kind
    ("t2m_C", "2 m temperature", "line"),
    ("precip_mm", "Precipitation", "bar"),
    ("wind_speed_ms", "10 m wind speed", "line"),
    ("msl_hPa", "Mean sea level pressure", "line"),
    ("tcc_pct", "Total cloud cover", "line"),
]


def attribution_text(year: int | None = None) -> str:
    return f"Data: © {year or datetime.now(timezone.utc).year} ECMWF, CC BY 4.0"


def meteogram_panels(point: dict) -> list[dict]:
    rows = point["series"]
    out = []
    for key, title, kind in PANELS:
        if key in point.get("units", {}) and all(key in r for r in rows):
            out.append(
                {
                    "key": key,
                    "title": title,
                    "kind": kind,
                    "units": point["units"][key],
                    "values": [
                        float("nan") if r[key] is None else r[key] for r in rows
                    ],
                }
            )
    return out


def _times(point):
    import numpy as np

    return np.array(
        [r["valid_time"].rstrip("Z") for r in point["series"]], dtype="datetime64[m]"
    )


def plot_meteogram(point: dict, output: str) -> None:
    import earthkit.plots as ekp
    import xarray as xr

    panels = meteogram_panels(point)
    if not panels:
        raise ValueError(
            "no plottable series in JSON (expected odpoint.py --json output)"
        )
    t = _times(point)
    fig = ekp.Figure(rows=len(panels), columns=1, figsize=(9, 2.3 * len(panels)))
    for p in panels:
        da = xr.DataArray(
            p["values"],
            dims=["valid_time"],
            coords={"valid_time": t},
            name=p["key"],
            attrs={"units": p["units"].split(" ")[0], "long_name": p["title"]},
        )
        ts = fig.add_timeseries()
        (ts.bar if p["kind"] == "bar" else ts.line)(da)
        ts.title(f"{p['title']} ({p['units']})")
    gp = point.get("gridpoint", {})
    fig.title(
        f"ECMWF {point.get('model', '').upper()} run {point.get('run', '')} — "
        f"{gp.get('lat')}, {gp.get('lon')}"
    )
    fig.save(output)
    _stamp(output)


def plot_map(
    path: str,
    param: str,
    step: int | None,
    output: str,
    units=None,
    domain=None,
    level=None,
) -> None:
    import earthkit.data as ekd
    import earthkit.plots as ekp

    fl = ekd.from_source("file", path).to_fieldlist()
    sel = fl.sel({"metadata.shortName": param})
    if len(sel) == 0:
        have = sorted({f.get("metadata.shortName") for f in fl})
        raise ValueError(f"param {param!r} not in file; available: {', '.join(have)}")
    if step is not None:
        sel = [f for f in sel if int(f.time.step().total_seconds() // 3600) == step]
    if level is not None:
        sel = [f for f in sel if f.get("metadata.level", default=None) == level]
    if not sel:
        raise ValueError("no field matches --step/--level")
    kw = {k: v for k, v in {"units": units, "domain": domain}.items() if v}
    fig = ekp.quickplot(sel[0], **kw)
    fig.save(output)
    _stamp(output)


def _stamp(output: str) -> None:
    """Add the attribution line to the saved figure (bottom-right)."""
    import matplotlib.pyplot as plt

    fig = plt.gcf()
    fig.text(
        0.99,
        -0.01,  # below the axes; bbox_inches="tight" expands the canvas to include it
        attribution_text(),
        ha="right",
        va="top",
        fontsize=7,
        color="#444",
    )
    fig.savefig(output, dpi=fig.dpi, bbox_inches="tight")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("map")
    m.add_argument("path")
    m.add_argument("--param", required=True, help="GRIB shortName, e.g. 2t, msl, tp")
    m.add_argument("--step", type=int)
    m.add_argument("--level", type=int, help="pressure level for pl fields")
    m.add_argument("--units", help="e.g. celsius, hPa, mm")
    m.add_argument("--domain", help="e.g. Europe, 'United Kingdom', [W, E, S, N]")
    m.add_argument("-o", "--output", required=True)
    g = sub.add_parser("meteogram")
    g.add_argument("json_path", help="output of open-data odpoint.py --json")
    g.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if a.cmd == "map":
                plot_map(a.path, a.param, a.step, a.output, a.units, a.domain, a.level)
            else:
                with open(a.json_path) as fh:
                    plot_meteogram(json.load(fh), a.output)
    except ImportError as e:
        print(
            f"error: {e}. Run with `uv run ekplot.py …` or pip install 'earthkit-plots>=1.0'",
            file=sys.stderr,
        )
        return 3
    except (ValueError, OSError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    print(f"saved {a.output} ({attribution_text()})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
