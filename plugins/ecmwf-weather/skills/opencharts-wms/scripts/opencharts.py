#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""ECMWF OpenCharts — find and download official ECMWF forecast charts (PNG/PDF). Stdlib only.

  python3 opencharts.py search "2 m temperature"           # product names
  python3 opencharts.py search temperature --model aifs
  python3 opencharts.py options medium-2t-wind              # base times, steps, projections
  python3 opencharts.py get medium-2t-wind --step 48 --projection opencharts_europe -o chart.png

No account needed. Charts are CC BY 4.0 (© ECMWF); keep the attribution with the image.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

API = "https://charts.ecmwf.int/opencharts-api/v1"
USER_AGENT = "ecmwf-weather-skill/0.1 (+https://github.com/ecmwf/ecmwf-weather-skill)"
# Chart rendering on first request can take ~10-20 s; images are 1-2 MB.
TIMEOUT_S = 90


def _get_json(url: str) -> dict:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:  # API returns JSON errors with 4xx
        try:
            body = json.loads(e.read())
        except ValueError:
            raise RuntimeError(f"HTTP {e.code} for {url}") from e
        raise RuntimeError(f"HTTP {e.code}: {body.get('error') or body}") from e


def _get_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return r.read()


# --- catalogue --------------------------------------------------------------------------------


def search(products: list[dict], text: str = "", model: str | None = None) -> list[dict]:
    """All words of `text` must appear in name+title; best (shortest-name) matches first."""
    words = [w.lower() for w in text.replace("metre", "m").split()]
    out = []
    for p in products:
        name = p["name"]
        hay = (
            f"{name} {p.get('title', '')}".lower()
            .replace("metre", "m")
            .replace("2t", "2 m temperature")
        )
        if words and not all(w in hay for w in words):
            continue
        if model == "aifs" and not name.startswith("aifs"):
            continue
        if model == "ifs" and name.startswith("aifs"):
            continue
        out.append(p)
    return sorted(out, key=lambda p: (p["name"].startswith("aifs"), len(p["name"])))


def parse_schema(schema: dict) -> dict:
    path = next(k for k in schema["paths"] if k.startswith("/products/"))
    params = {p["name"]: p["schema"] for p in schema["paths"][path]["get"].get("parameters", [])}

    def enum(n):
        return params.get(n, {}).get("enum", [])

    return {
        "base_times": enum("base_time"),
        "steps": [int(s) for s in enum("step")],
        "valid_times": enum("valid_time"),
        "projections": enum("projection"),
        "formats": enum("format"),
        "layer_names": enum("layer_name"),
    }


def validate_choice(what: str, value, allowed: list) -> None:
    if allowed and value not in allowed:
        raise ValueError(
            f"{what} {value!r} not available; choose from: {', '.join(map(str, allowed))}"
        )


def product_url(
    name: str,
    base_time: str | None = None,
    step: int | None = None,
    valid_time: str | None = None,
    projection: str | None = None,
    fmt: str = "png",
) -> str:
    q = {
        k: v
        for k, v in {
            "base_time": base_time,
            "step": step,
            "valid_time": valid_time,
            "projection": projection,
            "format": fmt,
        }.items()
        if v is not None
    }
    return f"{API}/products/{name}/?{urlencode(q)}"


def image_link(resp: dict) -> tuple[str, dict]:
    if "data" not in resp:
        raise RuntimeError(f"OpenCharts error: {resp.get('error') or resp}")
    return resp["data"]["link"]["href"], resp.get("meta", {})


def attribution() -> str:
    return (
        f"© {datetime.now(timezone.utc).year} European Centre for Medium-Range Weather Forecasts "
        "(ECMWF), CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"
    )


# --- CLI --------------------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sp = sub.add_parser("search")
    sp.add_argument("text", nargs="?", default="")
    sp.add_argument("--model", choices=["ifs", "aifs"])
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("options")
    sp.add_argument("product")
    sp.add_argument("--json", action="store_true")
    sp = sub.add_parser("get")
    sp.add_argument("product")
    sp.add_argument("--base-time", help="ISO, e.g. 2026-10-02T00:00:00Z (default: latest)")
    sp.add_argument("--step", type=int, help="hours after base time")
    sp.add_argument("--valid-time", help="ISO valid time (instead of --step)")
    sp.add_argument("--projection", default="opencharts_europe")
    sp.add_argument("--format", default="png", choices=["png", "pdf"])
    sp.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)

    try:
        if a.cmd == "search":
            hits = search(
                _get_json(f"{API}/search/?package=opencharts")["results"],
                a.text,
                a.model,
            )
            if a.json:
                print(
                    json.dumps(
                        [{"name": p["name"], "title": p["title"]} for p in hits],
                        indent=2,
                    )
                )
            else:
                for p in hits[:40]:
                    print(f"{p['name']:45} {p['title']}")
                print(f"\n{len(hits)} product(s). Options: opencharts.py options <name>")
            return 0

        opts = parse_schema(_get_json(f"{API}/schema/?package=openchart&product={a.product}"))
        if a.cmd == "options":
            if a.json:
                print(json.dumps(opts, indent=2))
            else:
                base_times = opts["base_times"]
                print(f"base times : {', '.join(base_times[:4])} … ({len(base_times)})")
                print(
                    f"steps      : {opts['steps'][0]}..{opts['steps'][-1]} ({len(opts['steps'])})"
                )
                print(f"projections: {', '.join(opts['projections'])}")
                if opts["layer_names"]:
                    print(f"layers     : {', '.join(opts['layer_names'])}")
            return 0

        validate_choice("projection", a.projection, opts["projections"])
        if a.base_time:
            validate_choice("base time", a.base_time, opts["base_times"])
        if a.step is not None:
            validate_choice("step", a.step, opts["steps"])
        resp = _get_json(
            product_url(a.product, a.base_time, a.step, a.valid_time, a.projection, a.format)
        )
        link, _meta = image_link(resp)
        Path(a.output).write_bytes(_get_bytes(link))
        desc = resp["data"].get("attributes", {}).get("description", "")
        print(f"saved {a.output}\n{desc}\nAttribution: {attribution()}")
        return 0
    except (
        ValueError,
        RuntimeError,
        OSError,
        urllib.error.URLError,
        KeyError,
        StopIteration,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
