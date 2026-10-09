#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "earthkit-plots>=1.0",
#   "earthkit-data>=1.2",
#   "earthkit-transforms[all]>=1.0",
# ]
# ///
"""Plot ECMWF data with earthkit-plots.

  map        one field from a GRIB/NetCDF file on a map (ECMWF style chosen automatically)
  ens-stats  ensemble mean / standard deviation / percentile maps from ensemble members
             (statistics with earthkit-transforms, maps with earthkit-plots)
  meteogram  multi-panel time series from point-forecast JSON
             (odpoint.py / ptpoint.py --json); an optional "observations" block
             (ecmwf-observations obs.py verify --meteogram-json) is drawn as dots

  uv run ekplot.py map forecast.grib2 --param 2t --step 24 --units celsius \\
      --domain Europe -o t2m.png
  uv run ekplot.py ens-stats ens.grib2 --param 2t --units celsius --domain Europe -o ens.png
      # -> ens_mean.png, ens_std.png (add --stats mean,std,p10,p90 and --panel for one figure)
  uv run ekplot.py meteogram lisbon.json -o lisbon.png
  uv run ekplot.py map forecast.grib2 --param 2t --step 12 --units celsius \\
      --obs stations.json -o t2m_obs.png   # station values (obs.py points) as dots

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
    a rate drawn as a bar spanning its interval is. Rates come from earthkit-transforms
    (`accumulation_to_rate`, per-interval amounts). The first row has no preceding interval.
    """
    import numpy as np
    import xarray as xr
    from earthkit.transforms import temporal

    rows = point["series"]
    t = np.array([r["valid_time"].rstrip("Z") for r in rows], dtype="datetime64[ns]")

    def rate(key):
        da = xr.DataArray(
            [_nan(r[key]) for r in rows],
            dims=["valid_time"],
            coords={"valid_time": t},
            attrs={"units": "mm"},
        )
        r = temporal.accumulation_to_rate(da, accumulation_type="start_of_step", rate_units="hours")
        return [round(float(v), 3) for v in r.values[1:]]

    has_band = all("precip_mm_p10" in r and "precip_mm_p90" in r for r in rows)
    widths = [int(b["step"] - a["step"]) for a, b in itertools.pairwise(rows)]
    return {
        "starts": [r["valid_time"].rstrip("Z") for r in rows[:-1]],
        "widths_h": widths,
        "rates": rate("precip_mm"),
        "band": (rate("precip_mm_p10"), rate("precip_mm_p90")) if has_band else None,
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


def observation_points(point: dict, key: str) -> tuple[list[str], list[float]]:
    """Observed (times, values) for a panel from the point JSON's optional `observations`."""
    rows = (point.get("observations") or {}).get("series", [])
    pts = [(r["valid_time"].rstrip("Z"), r[key]) for r in rows if r.get(key) is not None]
    return [t for t, _ in pts], [v for _, v in pts]


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
            bar = xr.DataArray(
                bars["rates"],
                dims=["valid_time"],
                coords={"valid_time": starts},
                name="precip_rate",
                attrs={"units": "mm/h", "long_name": p["title"]},
            )
            ts.bar(
                bar,
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
            ot, ov = observation_points(point, p["key"])
            if ot:
                ax.plot(
                    np.array(ot, dtype="datetime64[m]"),
                    ov,
                    "o",
                    color="black",
                    markersize=3,
                    label=point["observations"].get("label", "observed"),
                )
                ax.legend(loc="upper left", fontsize=8)
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
    fig.attribution(attribution_for(point))
    fig.save(output)
    return fig.fig


def _select(path: str, param: str, step: int | None = None, level: int | None = None):
    import earthkit.data as ekd

    fl = ekd.from_source("file", path).to_fieldlist()
    query = {"parameter.variable": param}
    if step is not None:
        query["time.step"] = step
    if level is not None:
        query["vertical.level"] = level
    sel = fl.sel(query)
    if len(sel) == 0:
        have = ", ".join(sorted(set(fl.metadata("shortName"))))
        raise ValueError(f"no field matches {query}; parameters in file: {have}")
    return sel


def plot_map(
    path: str,
    param: str,
    step: int | None,
    output: str,
    units=None,
    domain=None,
    level=None,
    obs=None,
) -> None:
    import earthkit.plots as ekp

    sel = _select(path, param, step, level)
    kw = {k: v for k, v in {"units": units, "domain": domain}.items() if v}
    fig = ekp.quickplot(sel[0], **kw)  # style="auto": ECMWF style from the GRIB metadata
    attribution = attribution_text()
    if obs:
        with open(obs) as fh:
            attribution += f"; stations: {overlay_stations(fig, json.load(fh))}"
    fig.attribution(attribution)
    fig.save(output)


def overlay_stations(fig, obs: dict) -> str:
    """Station values (ecmwf-observations `obs.py points` JSON) as dots coloured with the
    field's own style, so a dot matches the shading when forecast and observation agree."""
    import numpy as np
    from earthkit.utils.units import convert_units

    pts = obs.get("points", [])
    if not pts:
        raise ValueError("no station points in the observations JSON (obs.py points)")
    style = fig.layers[0].style
    values = np.array([p["value"] for p in pts], float)
    if obs.get("units") and getattr(style, "units", None):
        values = convert_units(values, style.units, source_units=obs["units"])
    lons, lats = (np.array([p[k] for p in pts], float) for k in ("lon", "lat"))
    fig.scatter(x=lons, y=lats, z=values, style=style, edgecolors="black", s=40, zorder=5)
    return obs.get("source", "observations")


# --- ensemble statistics -------------------------------------------------------------------------

STAT_NAMES = {"mean": "ensemble mean", "std": "ensemble standard deviation"}
# Spread maps: levels in the field's (converted) units, a sequential palette.
SPREAD_COLORS = "Purples"


def parse_stats(spec: str) -> list[tuple[str, int | None]]:
    out = []
    for item in spec.split(","):
        item = item.strip()
        if item in STAT_NAMES:
            out.append((item, None))
        elif item.startswith("p") and item[1:].isdigit() and 0 < int(item[1:]) < 100:
            out.append((item, int(item[1:])))
        else:
            raise ValueError(f"unknown statistic {item!r}; use mean, std or pNN (e.g. p10, p90)")
    return out


def stat_label(stat: str, long_name: str) -> str:
    if stat in STAT_NAMES:
        return f"{long_name} — {STAT_NAMES[stat]}"
    return f"{long_name} — ensemble {int(stat[1:])}th percentile"


def stat_outputs(output: str, stats: list[str]) -> dict[str, str]:
    from pathlib import Path

    p = Path(output)
    return {s: str(p.with_name(f"{p.stem}_{s}{p.suffix}")) for s in stats}


def ensemble_stats(path: str, param: str, stats, step: int | None = None):
    """{stat: DataArray} computed with earthkit-transforms over the member dimension."""
    import earthkit.transforms as ekt
    from earthkit.transforms import ensemble

    ds = _select(path, param, step).to_xarray()
    var = next(iter(ds.data_vars))
    da = ds[var]
    if "member" not in da.dims:
        raise ValueError("no ensemble members in the file (expected several 'number' values)")
    out = {}
    for name, q in stats:
        if name == "mean":
            r = ensemble.mean(da)
        elif name == "std":
            r = ensemble.std(da)
        else:
            r = ekt.reduce(da, how="percentile", dim="member", q=q)
        out[name] = r.squeeze(drop=True)
    return out, int(da.sizes["member"]), da.attrs.get("long_name", var)


def plot_ens_stats(
    path: str,
    param: str,
    output: str,
    stats_spec: str = "mean,std",
    units=None,
    domain=None,
    step=None,
    panel=False,
) -> list[str]:
    import earthkit.plots as ekp

    stats = parse_stats(stats_spec)
    fields, n, long_name = ensemble_stats(path, param, stats, step)
    # A spread is a difference: K and °C are the same size, so label it in the input units.
    spread_units = str(fields.get("std", next(iter(fields.values()))).attrs.get("units", ""))
    spread_units = {"kelvin": "K", "degree_Celsius": "°C"}.get(spread_units, spread_units)

    def draw(m, name, da):
        if name == "std":
            # Spread keeps the variable's metadata: give it its own levels and label so the
            # temperature style (or a °C offset) is never applied to a difference.
            m.contourf(da, colors=SPREAD_COLORS, extend="max")
            m.legend(label=f"{stat_label(name, long_name)} ({spread_units})")
            m.coastlines()
            m.borders()
            m.gridlines()
            return
        else:
            m.contourf(da, units=units, style="auto") if units else m.contourf(da, style="auto")
        m.coastlines()
        m.borders()
        m.gridlines()
        m.legend(label=stat_label(name, long_name))

    title = f"{long_name}, {n} members" + ("" if step is None else f", T+{step}h")
    if panel:
        fig = ekp.Figure(rows=1, columns=len(stats), size=(6 * len(stats), 4.5))
        for i, (name, _) in enumerate(stats):
            draw(fig.add_map(0, i, domain=domain), name, fields[name])
        fig.title(title)
        fig.attribution(attribution_text())
        fig.save(output)
        return [output]
    files = []
    for name, target in stat_outputs(output, [n for n, _ in stats]).items():
        fig = ekp.Figure(rows=1, columns=1, size=(8, 6))
        draw(fig.add_map(0, 0, domain=domain), name, fields[name])
        fig.title(f"{title} — {STAT_NAMES.get(name, name)}")
        fig.attribution(attribution_text())
        fig.save(target)
        files.append(target)
    return files


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
    m.add_argument("--obs", help="station values to overlay (ecmwf-observations obs.py points)")
    m.add_argument("-o", "--output", required=True)
    e = sub.add_parser("ens-stats", help="ensemble mean/std/percentile maps (earthkit-transforms)")
    e.add_argument("path")
    e.add_argument("--param", required=True, help="GRIB shortName, e.g. 2t")
    e.add_argument("--stats", default="mean,std", help="comma list: mean, std, pNN")
    e.add_argument("--step", type=int)
    e.add_argument("--units", help="e.g. celsius (not applied to the spread)")
    e.add_argument("--domain", help="e.g. Europe")
    e.add_argument("--panel", action="store_true", help="one figure with all statistics")
    e.add_argument("-o", "--output", required=True, help="x.png -> x_mean.png, x_std.png, …")
    g = sub.add_parser("meteogram")
    g.add_argument("json_path", help="output of open-data odpoint.py --json")
    g.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            if a.cmd == "map":
                plot_map(a.path, a.param, a.step, a.output, a.units, a.domain, a.level, a.obs)
            elif a.cmd == "ens-stats":
                files = plot_ens_stats(
                    a.path, a.param, a.output, a.stats, a.units, a.domain, a.step, a.panel
                )
            else:
                with open(a.json_path) as fh:
                    plot_meteogram(json.load(fh), a.output)
    except ImportError:
        return blocked(
            {
                "blocked": "the earthkit components this script needs are not installed",
                "why": "decoding GRIB needs earthkit (eccodes wheels for Linux/macOS).",
                "user_steps": [
                    "1. Install uv: https://docs.astral.sh/uv/getting-started/installation/ "
                    "(curl -LsSf https://astral.sh/uv/install.sh | sh), then re-run with "
                    "`uv run ekplot.py …` — it installs exactly what is needed.",
                    "2. Without uv: pip install 'earthkit-plots>=1.0' 'earthkit-data>=1.2' "
                    "'earthkit-transforms[all]>=1.0'",
                    "3. On Windows use WSL (earthkit has no Windows wheels).",
                ],
                "agent": "Run the script with `uv run`; if uv cannot be installed, give these "
                "steps.",
            },
            code=3,
        )
    except (ValueError, OSError, KeyError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    label = attribution_text()
    if a.cmd == "ens-stats":
        import earthkit.data as ekd

        with contextlib.redirect_stdout(sys.stderr):
            n = len(
                ekd.from_source("file", a.path).to_fieldlist().sel({"parameter.variable": a.param})
            )
        print(
            f"saved {', '.join(files)} (members: {n}; statistics with earthkit-transforms, "
            f"maps with earthkit-plots; {label})"
        )
        return 0
    if a.cmd == "meteogram":
        with open(a.json_path) as fh:
            label = attribution_for(json.load(fh))
    print(f"saved {a.output} ({label})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
