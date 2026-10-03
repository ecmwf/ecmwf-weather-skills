#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.10"
# dependencies = ["earthkit-plots>=1.0", "earthkit-data>=1.2"]
# ///
"""Plot ECMWF data with earthkit-plots.

  map        one field from a GRIB/NetCDF file on a map
  meteogram  multi-panel time series from point-forecast JSON (open-data skill's odpoint.py --json)

  uv run ekplot.py map forecast.grib2 --param 2t --step 24 --units celsius \\
      --domain Europe -o t2m.png
  uv run ekplot.py meteogram lisbon.json -o lisbon.png

Every figure carries the ECMWF CC-BY-4.0 attribution line.
"""

from __future__ import annotations

import argparse
import contextlib
import itertools
import json
import sys
from datetime import datetime, timezone

PANELS = [  # key in point JSON, title, kind, colour
    ("t2m_C", "2 m temperature", "line", "#d95c37"),
    ("precip_mm", "Precipitation rate", "bar", "#278ac5"),
    ("wind_speed_ms", "10 m wind speed", "line", "#247c68"),
    ("msl_hPa", "Mean sea level pressure", "line", "#7552a0"),
    ("tcc_pct", "Total cloud cover", "line", "#64748b"),
]


def attribution_text(year: int | None = None) -> str:
    return f"Data: © {year or datetime.now(timezone.utc).year} ECMWF, CC BY 4.0"


def attribution_for(point: dict) -> str:
    """Use the licence line the data carries (operational data isn't CC BY); default Open Data."""
    return (point.get("attribution") or {}).get("short") or attribution_text()


def _nan(v):
    return float("nan") if v is None else v


def precip_bars(point: dict) -> dict:
    """Precipitation per interval as a rate (mm/h) with the interval's start and length.

    Steps get longer with lead time (1 h → 3 h → 6 h), so interval totals are not comparable;
    a rate drawn as a bar spanning its interval is. The first row has no preceding interval.
    """
    rows = point["series"]
    starts, widths, rates, lo, hi = [], [], [], [], []
    has_band = all("precip_mm_p10" in r and "precip_mm_p90" in r for r in rows)
    for prev, cur in itertools.pairwise(rows):
        hours = cur["step"] - prev["step"]
        starts.append(prev["valid_time"].rstrip("Z"))
        widths.append(hours)
        rates.append(round(_nan(cur["precip_mm"]) / hours, 3))
        if has_band:
            lo.append(_nan(cur["precip_mm_p10"]) / hours)
            hi.append(_nan(cur["precip_mm_p90"]) / hours)
    return {
        "starts": starts,
        "widths_h": widths,
        "rates": rates,
        "band": (lo, hi) if has_band else None,
    }


def meteogram_panels(point: dict) -> list[dict]:
    rows = point["series"]
    out = []
    for key, title, kind, colour in PANELS:
        if key in point.get("units", {}) and all(key in r for r in rows):
            panel = {
                "key": key,
                "title": title,
                "kind": kind,
                "colour": colour,
                "units": "mm/h" if key == "precip_mm" else point["units"][key],
                "values": [_nan(r[key]) for r in rows],
            }
            lo, hi = f"{key}_p10", f"{key}_p90"  # ensemble output (polytope ptpoint.py --ensemble)
            if all(lo in r and hi in r for r in rows):
                panel["band"] = ([r[lo] for r in rows], [r[hi] for r in rows])
            out.append(panel)
    return out


def _times(point):
    import numpy as np

    return np.array([r["valid_time"].rstrip("Z") for r in point["series"]], dtype="datetime64[m]")


def plot_meteogram(point: dict, output: str):
    """Multi-panel meteogram on one shared time axis; returns the matplotlib figure."""
    import earthkit.plots as ekp
    import matplotlib.dates as mdates
    import numpy as np
    import xarray as xr

    panels = meteogram_panels(point)
    if not panels:
        raise ValueError(
            "no plottable series in JSON (expected odpoint.py/ptpoint.py --json output)"
        )
    t = _times(point)
    tz = point.get("timezone")
    zone = None
    if tz:
        from zoneinfo import ZoneInfo

        zone = ZoneInfo(tz)
    fig = ekp.Figure(rows=len(panels), columns=1, figsize=(10, 2.2 * len(panels)))
    axes = []
    for p in panels:
        ts = fig.add_timeseries()
        ax = ts.ax
        title = p["title"]
        if p["key"] == "precip_mm":
            bars = precip_bars(point)
            starts = np.array(bars["starts"], dtype="datetime64[m]")
            widths = np.array(bars["widths_h"]) / 24.0  # matplotlib date units are days
            ax.bar(
                starts,
                bars["rates"],
                width=widths,
                align="edge",
                color=p["colour"],
                alpha=0.85,
                edgecolor="white",
                linewidth=0.5,
            )
            if bars["band"]:
                ax.errorbar(
                    starts + (np.array(bars["widths_h"]) * 30).astype("timedelta64[m]"),
                    bars["rates"],
                    yerr=[
                        np.subtract(bars["rates"], bars["band"][0]),
                        np.subtract(bars["band"][1], bars["rates"]),
                    ],
                    fmt="none",
                    ecolor="#1b4f72",
                    linewidth=0.8,
                )
                title += " — median, whiskers 10-90 %"
            ax.set_ylim(bottom=0)
        else:
            da = xr.DataArray(
                p["values"],
                dims=["valid_time"],
                coords={"valid_time": t},
                name=p["key"],
                attrs={"units": p["units"], "long_name": p["title"]},
            )
            if "band" in p:  # ensemble: shade the 10-90 % range, draw the median
                lo, hi = (da.copy(data=v) for v in p["band"])
                ts.fill_between(lo, hi, alpha=0.3, color=p["colour"])
                title += " — median and 10-90 % range"
            ts.line(da, color=p["colour"], linewidth=1.8)
            if p["key"] == "tcc_pct":
                ax.set_ylim(0, 100)
        ax.set_title(f"{title} ({p['units']})", loc="left", fontsize=10)
        axes.append(ax)
    for ax in axes:  # one time axis for every panel
        ax.set_xlim(t[0], t[-1])
        ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12], tz=zone))
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%a %d %b\n%H:%M", tz=zone))
        ax.tick_params(axis="x", labelbottom=ax is axes[-1])
        ax.grid(True, alpha=0.4)
    axes[-1].set_xlabel(f"Local time ({tz})" if tz else "Time (UTC)")
    gp = point.get("gridpoint", {})
    fig.title(
        f"ECMWF {point.get('model', '').upper()} run {point.get('run', '')} — "
        f"{gp.get('lat')}, {gp.get('lon')}"
    )
    fig.save(output)
    _stamp(output, attribution_for(point))
    return fig.fig


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


def _stamp(output: str, text: str | None = None) -> None:
    """Add the attribution line to the saved figure (bottom-right)."""
    import matplotlib.pyplot as plt

    fig = plt.gcf()
    fig.text(
        0.99,
        -0.01,  # below the axes; bbox_inches="tight" expands the canvas to include it
        text or attribution_text(),
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
    label = attribution_text()
    if a.cmd == "meteogram":
        with open(a.json_path) as fh:
            label = attribution_for(json.load(fh))
    print(f"saved {a.output} ({label})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
