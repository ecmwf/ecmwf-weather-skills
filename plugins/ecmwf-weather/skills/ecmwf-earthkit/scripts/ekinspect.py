#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.12"
# dependencies = ["earthkit-data>=1.2"]
# ///
"""Summarise a GRIB or NetCDF file with earthkit-data (only component needed, ~64 MB).

  uv run ekinspect.py forecast.grib2
  uv run ekinspect.py forecast.grib2 --json

Reports parameters (short name, units, level type, levels, steps), base and valid times and the
grid, so you know what to select before processing or plotting.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys


def _iso(dt) -> str:
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def _meta(field, key, default=None):
    """GRIB/NetCDF metadata value of one field (earthkit-data 1.x API)."""
    return field.get(f"metadata.{key}", default=default)


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


def summarise(fl) -> dict:
    params: dict[str, dict] = {}
    base, valid = set(), set()
    for f in fl:
        name = _meta(f, "shortName")
        p = params.setdefault(
            name,
            {
                "units": _meta(f, "units", default=None),
                "name": _meta(f, "name", default=None),
                "levtype": _meta(f, "levtype", default=None),
                "levels": set(),
                "steps": set(),
                "members": set(),
            },
        )
        lev = _meta(f, "level", default=None)
        if p["levtype"] in ("pl", "sol", "ml") and lev is not None:
            p["levels"].add(int(lev))
        p["steps"].add(int(f.time.step().total_seconds() // 3600))
        num = _meta(f, "number", default=None)
        if num not in (None, 0):
            p["members"].add(int(num))
        base.add(f.time.base_datetime())
        valid.add(f.time.valid_datetime())

    first = fl[0].geography
    grid = {"shape": list(first.shape()), "type": first.grid_type()}
    with contextlib.suppress(Exception):
        bb = first.bounding_box()
        grid["bbox"] = {
            "north": bb.north,
            "west": bb.west,
            "south": bb.south,
            "east": bb.east,
        }

    for p in params.values():
        for k in ("levels", "steps", "members"):
            p[k] = sorted(p[k])
        if not p["members"]:
            del p["members"]
        if not p["levels"]:
            del p["levels"]

    return {
        "fields": len(fl),
        "params": params,
        "base_times": sorted(_iso(b) for b in base),
        "valid_time_range": [_iso(min(valid)), _iso(max(valid))],
        "grid": grid,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("path")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    try:
        import earthkit.data as ekd
    except ImportError:
        return blocked(
            {
                "blocked": "the earthkit components this script needs are not installed",
                "why": "decoding GRIB needs earthkit (eccodes wheels for Linux/macOS).",
                "user_steps": [
                    "1. Install uv: https://docs.astral.sh/uv/getting-started/installation/ "
                    "(curl -LsSf https://astral.sh/uv/install.sh | sh), then re-run with "
                    "`uv run ekinspect.py …` — it installs exactly what is needed.",
                    "2. Without uv: pip install 'earthkit-data>=1.2'",
                    "3. On Windows use WSL (earthkit has no Windows wheels).",
                ],
                "agent": "Run the script with `uv run`; if uv cannot be installed, give these "
                "steps.",
            },
            a.json,
            code=3,
        )
    with contextlib.redirect_stdout(sys.stderr):
        fl = ekd.from_source("file", a.path).to_fieldlist()
        s = summarise(fl)
    if a.json:
        print(json.dumps(s, indent=2))
        return 0
    print(f"{a.path}: {s['fields']} fields, grid {s['grid']['type']} {s['grid']['shape']}")
    first_valid, last_valid = s["valid_time_range"][0], s["valid_time_range"][1]
    print(f"base time(s): {', '.join(s['base_times'])}; valid {first_valid} .. {last_valid}")
    for k, p in s["params"].items():
        extra = f" levels {p['levels']}" if "levels" in p else ""
        extra += f" members {len(p['members'])}" if "members" in p else ""
        print(
            f"  {k:8} {p['name'] or '':35} [{p['units']}] {p['levtype']} steps {p['steps']}{extra}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
