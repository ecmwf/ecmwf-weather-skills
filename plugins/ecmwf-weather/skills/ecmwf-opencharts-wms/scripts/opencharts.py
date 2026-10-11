#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""ECMWF OpenCharts — official ECMWF forecast charts and meteograms (PNG/PDF). Stdlib only.

  python3 opencharts.py search "2 m temperature"           # product names
  python3 opencharts.py search temperature --model aifs
  python3 opencharts.py options medium-2t-wind              # base times, steps, projections
  python3 opencharts.py get medium-2t-wind --step 48 --projection opencharts_europe -o chart.png
  python3 opencharts.py get opencharts_meteogram --lat 44.49 --lon 11.34 -o bologna.png

Charts drawn for one place (`search meteogram`) need --lat and --lon; the ENS meteogram's
--kind is 10-day (default), 15-day, 15-day-with-climate, plume or wave. No account needed.
ECMWF charts are CC BY 4.0 (© ECMWF); CAMS charts carry the Copernicus credit instead. Keep
the attribution `get` prints with the image.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

# Never write __pycache__ into the installed skill (it may be read-only, and skills must
# not be modified); sibling scripts are imported below.
sys.dont_write_bytecode = True


API = "https://charts.ecmwf.int/opencharts-api/v1"
USER_AGENT = "ecmwf-weather-skills/0.1 (+https://github.com/ecmwf/ecmwf-weather-skills)"
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
    """A product's options. `point`: the chart is drawn for one place (its schema takes lat and
    lon — meteograms, vertical profiles…); `kinds`: its layouts (the API's `epsgram`)."""
    path = next(k for k in schema["paths"] if k.startswith("/products/"))
    params = {p["name"]: p["schema"] for p in schema["paths"][path]["get"].get("parameters", [])}

    def enum(n):
        return params.get(n, {}).get("enum", [])

    kinds = enum("epsgram")
    return {
        "base_times": enum("base_time"),
        "steps": [int(s) for s in enum("step")],
        "valid_times": enum("valid_time"),
        "projections": enum("projection"),
        "formats": enum("format"),
        "layer_names": enum("layer_name"),
        "point": "lat" in params and "lon" in params,
        "kinds": kinds,
        "kind_default": params.get("epsgram", {}).get("default", kinds[0] if kinds else None),
    }


def validate_choice(what: str, value, allowed: list) -> None:
    if allowed and value not in allowed:
        raise ValueError(
            f"{what} {value!r} not available; choose from: {', '.join(map(str, allowed))}"
        )


# --- charts for one place ---------------------------------------------------------------------

# The ENS meteogram layouts (`epsgram`) by the names the ECMWF MCP's charts_get_meteogram uses.
# Other meteograms prefix the same layouts (aifs_ens_classical_plume is the AIFS ENS plume).
KINDS = {
    "10-day": "classical_10d",
    "15-day": "classical_15d",
    "15-day-with-climate": "classical_15d_with_climate",
    "plume": "classical_plume",
    "wave": "classical_wave",
}


def _is_kind(value: str, token: str) -> bool:
    return value == token or value.endswith(f"_{token}")


def kind_name(value: str) -> str:
    """The name of an `epsgram` layout (classical_10d -> 10-day); other layouts keep theirs."""
    return next((name for name, token in KINDS.items() if _is_kind(value, token)), value)


def resolve_kind(kind: str, allowed: list[str], product: str) -> str:
    """The product's `epsgram` for a layout name (plume), the end of one (public) or its value."""
    if not allowed:
        raise ValueError(f"{product} has one layout: drop --kind")
    if kind in allowed:
        return kind
    token = KINDS.get(kind, kind)
    hits = [v for v in allowed if _is_kind(v, token)]
    if len(hits) == 1:
        return hits[0]
    names = ", ".join(kind_name(v) for v in allowed)
    raise ValueError(f"kind {kind!r} not available for {product}; choose from: {names}")


def check_place(
    product: str, point: bool, lat: float | None, lon: float | None
) -> tuple[float, float] | None:
    """The place of a chart drawn for one place; None for maps, which take no place. Asked
    without one, OpenCharts draws a default point (HTTP 200), so a missing place is an error."""
    if not point:
        if lat is not None or lon is not None:
            raise ValueError(
                f"{product} is not drawn for a place: drop --lat/--lon "
                "(charts for one place: opencharts.py search meteogram)"
            )
        return None
    if lat is None or lon is None:
        raise ValueError(
            f"{product} is drawn for one place: add --lat LAT --lon LON "
            "(decimal degrees, north and east positive)"
        )
    if not -90 <= lat <= 90:
        raise ValueError(f"latitude {lat} must be within -90..90")
    if not -180 <= lon <= 360:  # OpenCharts reads 180..360 as west longitudes
        raise ValueError(f"longitude {lon} must be within -180..360")
    return lat, lon


def _degrees(value: float, positive: str, negative: str) -> str:
    return f"{abs(value):.10g}°{positive if value >= 0 else negative}"


def run_label(base_time: str) -> str:
    """2026-10-11T00:00:00Z -> Sun 11 Oct 2026 00 UTC, as OpenCharts writes base times."""
    try:
        t = datetime.strptime(base_time, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:  # not the API's format: show it as given
        return base_time
    return t.strftime("%a %d %b %Y %H UTC")


def describe_place(
    title: str,
    place: tuple[float, float],
    kind: str | None,
    base: str | None,
    step: int | None = None,
    valid_time: str | None = None,
) -> list[str]:
    """What a chart for one place shows. OpenCharts sends no description for these: the base
    time is the run requested, the gridpoint used is named only in the chart's title, and the
    step is printed as asked (an EFI/CDF step ends a 24 h window, so no valid time is derived)."""
    what = f"{title} ({kind_name(kind)})" if kind else title
    where = f"{_degrees(place[0], 'N', 'S')} {_degrees(place[1], 'E', 'W')}"
    lines = [f"{what} for {where}; the chart title gives the model gridpoint used"]
    if base:
        at_step = f", step {step} h" if step is not None else ""
        lines.append(f"Base time: {run_label(base)}{at_step}")
    if valid_time:
        lines.append(f"Valid time: {valid_time}")
    return lines


def product_url(
    name: str,
    base_time: str | None = None,
    step: int | None = None,
    valid_time: str | None = None,
    projection: str | None = None,
    fmt: str = "png",
    lat: float | None = None,
    lon: float | None = None,
    epsgram: str | None = None,
) -> str:
    q = {
        k: v
        for k, v in {
            "base_time": base_time,
            "step": step,
            "valid_time": valid_time,
            "projection": projection,
            "lat": lat,
            "lon": lon,
            "epsgram": epsgram,
            "format": fmt,
        }.items()
        if v is not None
    }
    return f"{API}/products/{name}/?{urlencode(q)}"


# How many recent runs to try when the newest is still being produced.
MAX_RUNS_TRIED = 3


def fetch_product(
    name: str,
    base_times: list[str],
    step: int | None,
    projection: str | None,
    fmt: str = "png",
    get=None,
    **params,
) -> tuple[dict, str]:
    """Newest run that already has `step`: a run is listed as soon as its first steps exist,
    so for hours after each run starts, later steps are only in the previous run. `params`:
    lat, lon and epsgram of a chart drawn for one place."""
    get = get or _get_json
    last: Exception | None = None
    for base in base_times[:MAX_RUNS_TRIED]:
        try:
            return get(product_url(name, base, step, None, projection, fmt, **params)), base
        except RuntimeError as e:
            if "not available" not in str(e):
                raise
            last = e
    what = name if step is None else f"step {step}"
    raise RuntimeError(f"{what} not available in the latest runs: {last}")


def image_link(resp: dict) -> tuple[str, dict]:
    if "data" not in resp:
        raise RuntimeError(f"OpenCharts error: {resp.get('error') or resp}")
    href = resp["data"]["link"]["href"]
    if not href.startswith("https://"):  # the server picks this URL; never follow plain http
        raise RuntimeError(f"OpenCharts returned a non-https image link: {href}")
    return href, resp.get("meta", {})


# OpenCharts gives CAMS (Copernicus Atmosphere Monitoring Service) products the template "cams"
# and draws them from the composition_* layers; their responses carry no ECMWF licence. A CAMS
# point graph uses the ECMWF template but names CAMS (classical_cams_uvindex).
CAMS_WORD = re.compile(r"(?i)(?<![a-z])cams(?![a-z])")
CAMS_CREDIT = (
    "Generated using Copernicus Atmosphere Monitoring Service information {year}. Neither the "
    "European Commission nor ECMWF is responsible for any use that may be made of the Copernicus "
    "information or data it contains."
)


def is_cams(name: str, resp: dict | None = None, title: str = "", layers=()) -> bool:
    """Whether an OpenCharts product is a CAMS product, which needs the Copernicus credit."""
    return (
        (resp or {}).get("template") == "cams"
        or any(str(n).startswith("composition_") for n in layers)
        or bool(CAMS_WORD.search(f"{name} {title}"))
    )


def attribution(cams: bool = False) -> str:
    year = datetime.now(timezone.utc).year
    if cams:  # Copernicus products: the credit the Copernicus licence asks for
        return CAMS_CREDIT.format(year=year)
    return (
        f"© {year} European Centre for Medium-Range Weather Forecasts "
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
    sp.add_argument("--projection", help="map area (default: opencharts_europe); maps only")
    sp.add_argument("--lat", type=float, help="degrees north; charts drawn for one place")
    sp.add_argument("--lon", type=float, help="degrees east; charts drawn for one place")
    sp.add_argument(
        "--kind",
        help="meteogram layout: 10-day (default), 15-day, 15-day-with-climate, plume, wave",
    )
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
                base_times, steps = opts["base_times"], opts["steps"]
                print(f"base times : {', '.join(base_times[:4])} … ({len(base_times)})")
                print(
                    f"steps      : {f'{steps[0]}..{steps[-1]} ({len(steps)})' if steps else 'none'}"
                )
                print(f"projections: {', '.join(opts['projections']) or 'none'}")
                if opts["layer_names"]:
                    print(f"layers     : {', '.join(opts['layer_names'])}")
                if opts["point"]:
                    print("place      : --lat LAT --lon LON (required)")
                if opts["kinds"]:
                    names = ", ".join(kind_name(k) for k in opts["kinds"])
                    default = kind_name(opts["kind_default"])
                    print(f"kinds      : {names} (--kind; default {default})")
            return 0

        place = check_place(a.product, opts["point"], a.lat, a.lon)
        kind = a.kind or opts["kind_default"]
        params = {"epsgram": resolve_kind(kind, opts["kinds"], a.product) if kind else None}
        if place:
            params.update(lat=place[0], lon=place[1])
        # Options the product doesn't list would be ignored or answered "not available".
        for flag, value, listed in (
            ("--projection", a.projection, opts["projections"]),
            ("--step", a.step, opts["steps"]),
            ("--valid-time", a.valid_time, opts["valid_times"]),
        ):
            if value is not None and not listed:
                raise ValueError(f"{a.product} takes no {flag}: drop it")
        projection = a.projection
        if opts["projections"]:
            projection = projection or "opencharts_europe"
            validate_choice("projection", projection, opts["projections"])
        if a.base_time:
            validate_choice("base time", a.base_time, opts["base_times"])
        if a.step is not None:
            validate_choice("step", a.step, opts["steps"])
        if a.base_time or a.valid_time:
            url = product_url(
                a.product, a.base_time, a.step, a.valid_time, projection, a.format, **params
            )
            resp, base = _get_json(url), a.base_time
        else:
            resp, base = fetch_product(
                a.product, opts["base_times"], a.step, projection, a.format, **params
            )
        link, _meta = image_link(resp)
        Path(a.output).write_bytes(_get_bytes(link))
        attrs = resp["data"].get("attributes", {})
        cams = is_cams(a.product, resp, attrs.get("title", ""), opts["layer_names"])
        lines = [f"saved {a.output}"]
        if attrs.get("description"):
            lines.append(attrs["description"])
        if place:
            title = attrs.get("title") or a.product
            lines += describe_place(title, place, params["epsgram"], base, a.step, a.valid_time)
        lines.append(f"Attribution: {attribution(cams)}")
        print("\n".join(lines))
        return 0
    except (urllib.error.URLError, ConnectionError, TimeoutError) as e:
        if not isinstance(e, urllib.error.HTTPError):
            import ecmwf_status  # sibling script

            return blocked(ecmwf_status.network_barrier("opencharts", str(e)), code=2)
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (
        ValueError,
        RuntimeError,
        OSError,
        KeyError,
        StopIteration,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
