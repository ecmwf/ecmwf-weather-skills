#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10"]
# ///
"""ECMWF ecCharts WMS helper — layers, times, GetMap/GetLegend/GetFeatureInfo.

Standard library only, except Pillow for compositing a map image with background and coastlines
(`uv run` installs it; with plain python3 the image is the transparent data layer alone).

  python3 wms.py layers --public                       # what can I use without an account
  python3 wms.py layers --search temperature --json
  python3 wms.py times --layer msl_public
  python3 wms.py getmap --layer msl_public --bbox -15,33,35,65 \\
      --time 2026-10-03T12:00:00Z --url-only
  uv run  wms.py getmap --layer msl_public --bbox -15,33,35,65 --valid +24 -o mslp.png
  python3 wms.py info --layer msl_public --lat 51.5 --lon -0.1
  python3 wms.py legend --layer t850_public

The public token (`token=public`) needs no account. Non-public layers need an ECMWF API key
(ECMWF_API_KEY or ~/.ecmwfapirc) — it is redacted in all printed URLs.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

BASE = "https://eccharts.ecmwf.int/wms/"
NS = {"w": "http://www.opengis.net/wms", "xlink": "http://www.w3.org/1999/xlink"}
# Layers without a time dimension; sending TIME with them makes the server return an exception.
STATIC_LAYERS = {"background", "foreground", "grid", "rivers", "boundaries"}
USER_AGENT = "ecmwf-weather-skills/0.1 (+https://github.com/ecmwf/ecmwf-weather-skills)"
# GetCapabilities is ~600 KB; maps render server-side in a few seconds.
TIMEOUT_S = 60
# Half-width of the 3x3-pixel box used for GetFeatureInfo around the query point (degrees).
INFO_HALF_BOX_DEG = 0.01
MERC_R = 20037508.342789244  # half the EPSG:3857 world width in metres


# --- capabilities -----------------------------------------------------------------------------


def _text(el, path):
    x = el.find(path, NS)
    return x.text.strip() if x is not None and x.text else None


def parse_capabilities(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    out = []
    for layer in root.iter(f"{{{NS['w']}}}Layer"):
        name = _text(layer, "w:Name")
        if not name:
            continue
        time = None
        for d in layer.findall("w:Dimension", NS):
            if d.get("name") == "time":
                time = {"default": d.get("default"), "values": (d.text or "").strip()}
        styles = []
        for st in layer.findall("w:Style", NS):
            lg = st.find("w:LegendURL/w:OnlineResource", NS)
            styles.append(
                {
                    "name": _text(st, "w:Name"),
                    "title": _text(st, "w:Title"),
                    "legend": lg.get(f"{{{NS['xlink']}}}href") if lg is not None else None,
                }
            )
        out.append(
            {
                "name": name,
                "title": _text(layer, "w:Title"),
                "abstract": _text(layer, "w:Abstract"),
                "queryable": layer.get("queryable") == "1",
                "styles": styles,
                "time": time,
            }
        )
    return out


def _parse_iso(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _fmt_iso(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration(p: str) -> timedelta:
    m = re.fullmatch(r"P(?:(\d+)D)?(?:T(?:(\d+)H)?(?:(\d+)M)?)?", p)
    if not m:
        raise ValueError(f"unsupported ISO8601 period {p!r}")
    d, h, mi = (int(x or 0) for x in m.groups())
    return timedelta(days=d, hours=h, minutes=mi)


def expand_time(text: str) -> list[str]:
    """Expand a WMS time dimension ('t1,t2/t3/PT6H,...') into a sorted list of ISO times."""
    out: set[str] = set()
    for part in filter(None, (p.strip() for p in text.split(","))):
        if "/" in part:
            a, b, step = part.split("/")
            t, end, dt = _parse_iso(a), _parse_iso(b), _duration(step)
            while t <= end:
                out.add(_fmt_iso(t))
                t += dt
        else:
            out.add(part)
    return sorted(out)


def summarise(layer: dict) -> dict:
    s = {
        "name": layer["name"],
        "title": layer["title"],
        "queryable": layer["queryable"],
        "styles": [st["name"] for st in layer["styles"]],
    }
    if layer["time"]:
        times = expand_time(layer["time"]["values"])
        default = layer["time"]["default"] or times[-1]
        s.update(
            first_time=times[0],
            last_time=times[-1],
            default_time=default,
            times=len(times),
            forecast_hours_ahead=int(
                (_parse_iso(times[-1]) - _parse_iso(default)).total_seconds() // 3600
            ),
        )
    return s


def filter_layers(
    layers: list[dict], search: str | None = None, public: bool = False
) -> list[dict]:
    res = []
    for layer in layers:
        if public and not layer["name"].endswith("_public"):
            continue
        hay = " ".join(filter(None, [layer["name"], layer["title"], layer["abstract"]])).lower()
        if search and search.lower() not in hay:
            continue
        res.append(layer)
    return res


def closest_time(times: list[str], target: str) -> str:
    t = _parse_iso(target)
    return min(times, key=lambda x: abs((_parse_iso(x) - t).total_seconds()))


# --- URLs -------------------------------------------------------------------------------------


def _lonlat_to_3857(lon: float, lat: float) -> tuple[float, float]:
    lat = max(min(lat, 85.0511), -85.0511)  # Web Mercator limit
    x = lon * MERC_R / 180.0
    y = math.log(math.tan((90.0 + lat) * math.pi / 360.0)) / (math.pi / 180.0) * MERC_R / 180.0
    return x, y


def _bbox_param(bbox, crs: str) -> str:
    w, s, e, n = bbox
    if crs == "EPSG:4326":  # WMS 1.3.0 axis order for EPSG:4326 is lat,lon
        return f"{s:g},{w:g},{n:g},{e:g}"
    if crs in ("EPSG:3857", "EPSG:900913"):
        x0, y0 = _lonlat_to_3857(w, s)
        x1, y1 = _lonlat_to_3857(e, n)
        return f"{x0:.2f},{y0:.2f},{x1:.2f},{y1:.2f}"
    raise ValueError(f"unsupported CRS {crs}; use EPSG:3857 or EPSG:4326")


def _url(params: dict, token: str) -> str:
    return BASE + "?" + urlencode({"token": token, **params})


def getmap_url(
    layer: str,
    bbox,
    width: int = 800,
    height: int = 600,
    time: str | None = None,
    style: str = "",
    crs: str = "EPSG:3857",
    token: str = "public",
    transparent: bool = True,
) -> str:
    p = {
        "service": "WMS",
        "version": "1.3.0",
        "request": "GetMap",
        "layers": layer,
        "styles": style,
        "crs": crs,
        "bbox": _bbox_param(bbox, crs),
        "width": width,
        "height": height,
        "format": "image/png",
        "transparent": str(transparent).lower(),
    }
    if time and layer not in STATIC_LAYERS:
        p["time"] = time
    return _url(p, token)


def legend_url(layer: str, style: str = "", token: str = "public") -> str:
    return _url(
        {
            "request": "GetLegend",
            "layers": layer,
            "styles": style,
            "width": 350,
            "height": 50,
        },
        token,
    )


def featureinfo_url(
    layer: str, lat: float, lon: float, time: str | None = None, token: str = "public"
) -> str:
    d = INFO_HALF_BOX_DEG
    p = {
        "service": "WMS",
        "version": "1.3.0",
        "request": "GetFeatureInfo",
        "layers": layer,
        "query_layers": layer,
        "styles": "",
        "crs": "EPSG:4326",
        "bbox": f"{lat - d:g},{lon - d:g},{lat + d:g},{lon + d:g}",
        "width": 3,
        "height": 3,
        "i": 1,
        "j": 1,
        "info_format": "application/json",
    }
    if time:
        p["time"] = time
    return _url(p, token)


def redact(url: str) -> str:
    return re.sub(r"token=(?!public\b)[^&]+", "token=***", url)


# --- responses --------------------------------------------------------------------------------


def service_exception(content_type: str, body: bytes) -> str | None:
    """The server reports errors as HTTP 200 text/xml ServiceExceptionReport."""
    if "xml" not in (content_type or "") and not body.lstrip().startswith(b"<?xml"):
        return None
    m = re.search(
        rb'<ServiceException(?=[\s>])(?:\s+code="([^"]*)")?[^>]*>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</ServiceException>',
        body,
        re.S,
    )
    if not m:
        return body[:300].decode(errors="replace")
    code, msg = (m.group(1) or b"").decode(), m.group(2).decode().strip()
    return f"{code}: {msg}" if code else msg


def parse_featureinfo(text: str) -> list[dict]:
    d = json.loads(text)
    return [
        {
            "layer": p.get("Name"),
            "value": p.get("Value", {}).get("Data"),
            "unit": p.get("Value", {}).get("Unit"),
            "lat": p.get("Grid point latitude"),
            "lon": p.get("Grid point longitude"),
        }
        for p in d.get("Probes", [])
    ]


def fetch(url: str) -> tuple[str, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:  # follows the 302 to /streaming/
        return r.headers.get("Content-Type", ""), r.read()


def fetch_image(url: str) -> bytes:
    ctype, body = fetch(url)
    err = service_exception(ctype, body)
    if err:
        raise RuntimeError(f"WMS error — {err}")
    return body


def composite(images: list[bytes]) -> bytes:
    from PIL import Image  # optional; only for -o with background

    base = Image.open(io.BytesIO(images[0])).convert("RGBA")
    for b in images[1:]:
        base.alpha_composite(Image.open(io.BytesIO(b)).convert("RGBA"))
    out = io.BytesIO()
    base.save(out, format="PNG")
    return out.getvalue()


# --- tokens -----------------------------------------------------------------------------------


def resolve_token(arg: str | None) -> str:
    """'public' unless asked for 'key': then ECMWF_API_KEY or ~/.ecmwfapirc."""
    if arg != "key":
        return arg or "public"
    if os.environ.get("ECMWF_API_KEY"):
        return os.environ["ECMWF_API_KEY"]
    rc = Path.home() / ".ecmwfapirc"
    if rc.exists():
        return json.loads(rc.read_text())["key"]
    raise ValueError(
        "--token key needs ECMWF_API_KEY or ~/.ecmwfapirc (get a key at https://api.ecmwf.int/v1/key/)"
    )


def resolve_time(valid: str | None, time: str | None) -> str | None:
    if time:
        return time
    if valid:  # "+24" hours from now, rounded down to 6 h
        now = datetime.now(timezone.utc) + timedelta(hours=int(valid.lstrip("+")))
        return _fmt_iso(
            now.replace(minute=0, second=0, microsecond=0, hour=now.hour - now.hour % 6)
        )
    return None


ATTRIBUTION = "Map data: © {year} ECMWF, CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)"


def attribution(layer: str) -> str:
    a = ATTRIBUTION.format(year=datetime.now(timezone.utc).year)
    if layer.startswith("composition_"):
        a += ". Generated using Copernicus Atmosphere Monitoring Service information."
    return a


# --- CLI --------------------------------------------------------------------------------------


def _join_negative_values(argv: list[str]) -> list[str]:
    """Accept `--bbox -10,35,30,60` (argparse would read -10,... as an option)."""
    out, i = [], 0
    while i < len(argv):
        if (
            argv[i] in ("--bbox", "--lon", "--lat")
            and i + 1 < len(argv)
            and re.match(r"-\d", argv[i + 1])
        ):
            out.append(f"{argv[i]}={argv[i + 1]}")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


def _load_caps(a) -> list[dict]:
    if a.caps:
        return parse_capabilities(Path(a.caps).read_text())
    url = _url(
        {"service": "WMS", "version": "1.3.0", "request": "GetCapabilities"},
        resolve_token(a.token),
    )
    return parse_capabilities(fetch(url)[1].decode())


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--token", help="'public' (default) or 'key' to use your ECMWF API key")
        sp.add_argument("--json", action="store_true")

    sp = sub.add_parser("layers", help="list layers")
    common(sp)
    sp.add_argument("--caps", help="read GetCapabilities XML from a file")
    sp.add_argument("--search")
    sp.add_argument("--public", action="store_true", help="only the *_public met layers")
    sp = sub.add_parser("times", help="valid times of a layer")
    common(sp)
    sp.add_argument("--caps")
    sp.add_argument("--layer", required=True)
    sp = sub.add_parser("getmap", help="map image or URL")
    common(sp)
    sp.add_argument("--layer", required=True)
    sp.add_argument("--style", default="")
    sp.add_argument("--bbox", required=True, help="W,S,E,N in degrees, e.g. -15,33,35,65")
    sp.add_argument("--crs", default="EPSG:3857", choices=["EPSG:3857", "EPSG:4326"])
    sp.add_argument("--width", type=int, default=1024)
    sp.add_argument("--height", type=int, default=768)
    sp.add_argument("--time", help="valid time ISO8601, e.g. 2026-10-03T12:00:00Z")
    sp.add_argument("--valid", help="valid time relative to now in hours, e.g. +24")
    sp.add_argument("--no-background", action="store_true", help="only the transparent data layer")
    sp.add_argument("--url-only", action="store_true")
    sp.add_argument("-o", "--output")
    sp = sub.add_parser("legend")
    common(sp)
    sp.add_argument("--layer", required=True)
    sp.add_argument("--style", default="")
    sp.add_argument("-o", "--output")
    sp = sub.add_parser("info", help="value at a point (GetFeatureInfo)")
    common(sp)
    sp.add_argument("--layer", required=True)
    sp.add_argument("--lat", type=float, required=True)
    sp.add_argument("--lon", type=float, required=True)
    sp.add_argument("--time")
    a = ap.parse_args(_join_negative_values(sys.argv[1:] if argv is None else argv))

    try:
        token = resolve_token(a.token)
        if a.cmd == "layers":
            ls = [summarise(layer) for layer in filter_layers(_load_caps(a), a.search, a.public)]
            if a.json:
                print(
                    json.dumps(
                        {
                            "layers": ls,
                            "token": "public" if token == "public" else "key",
                        },
                        indent=2,
                    )
                )
            else:
                for s in ls:
                    rng = (
                        f"valid {s['first_time']} .. {s['last_time']} "
                        f"(+{s['forecast_hours_ahead']} h)"
                        if "first_time" in s
                        else "static"
                    )
                    print(f"{s['name']:40} {s['title']}  [{rng}]")
                print(f"\n{len(ls)} layer(s). Attribution: {attribution('')}")
            return 0

        if a.cmd == "times":
            layer = next((c for c in _load_caps(a) if c["name"] == a.layer), None)
            if layer is None:
                raise ValueError(f"layer {a.layer!r} not found — list layers with `wms.py layers`")
            times = expand_time(layer["time"]["values"]) if layer["time"] else []
            data = {
                "layer": a.layer,
                "default": layer["time"]["default"] if layer["time"] else None,
                "times": times,
            }
            print(
                json.dumps(data, indent=2)
                if a.json
                else "\n".join(times) or "static layer (no time)"
            )
            return 0

        if a.cmd == "getmap":
            bbox = tuple(float(x) for x in a.bbox.split(","))
            time = resolve_time(a.valid, a.time)
            url = getmap_url(a.layer, bbox, a.width, a.height, time, a.style, a.crs, token)
            if a.url_only or not a.output:
                print(
                    json.dumps({"url": redact(url), "attribution": attribution(a.layer)})
                    if a.json
                    else redact(url)
                )
                return 0
            img = fetch_image(url)
            note = ""
            if not a.no_background:
                try:
                    bg = fetch_image(
                        getmap_url(
                            "background",
                            bbox,
                            a.width,
                            a.height,
                            crs=a.crs,
                            token=token,
                            transparent=False,
                        )
                    )
                    fg = fetch_image(
                        getmap_url(
                            "foreground",
                            bbox,
                            a.width,
                            a.height,
                            crs=a.crs,
                            token=token,
                        )
                    )
                    img = composite([bg, img, fg])
                except ImportError:
                    note = (
                        " (Pillow missing: data layer only — "
                        "run with `uv run` for background and coastlines)"
                    )
            Path(a.output).write_bytes(img)
            print(
                f"saved {a.output}{note}\nURL: {redact(url)}\nAttribution: {attribution(a.layer)}"
            )
            return 0

        if a.cmd == "legend":
            url = legend_url(a.layer, a.style, token)
            if a.output:
                Path(a.output).write_bytes(fetch_image(url))
                print(f"saved {a.output}")
            else:
                print(redact(url))
            return 0

        if a.cmd == "info":
            ctype, body = fetch(featureinfo_url(a.layer, a.lat, a.lon, a.time, token))
            err = service_exception(ctype, body)
            if err:
                raise RuntimeError(f"WMS error — {err}")
            vals = parse_featureinfo(body.decode())
            print(
                json.dumps({"values": vals, "attribution": attribution(a.layer)}, indent=2)
                if a.json
                else "\n".join(
                    f"{v['layer']}: {v['value']} {v['unit']} at {v['lat']},{v['lon']}" for v in vals
                )
            )
            return 0
    except (
        ValueError,
        RuntimeError,
        OSError,
        urllib.error.URLError,
        ET.ParseError,
    ) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    sys.exit(main())
