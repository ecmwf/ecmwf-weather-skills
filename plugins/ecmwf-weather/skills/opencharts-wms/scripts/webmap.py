#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Build and serve an ECMWF forecast web map with click-for-meteogram. Stdlib only.

  python3 webmap.py create webmap                       # writes webmap/{index.html,app.js,…}
  python3 webmap.py create webmap --layers msl_public,t850_public,ws850_public \\
      --center 50,10 --zoom 4
  python3 webmap.py serve webmap --port 8000            # http://127.0.0.1:8000

The page (Leaflet) shows ECMWF WMS layers with a time slider, legend and click values; it works
from any static host. `serve` adds /api/meteogram.png and /api/point.json: the first request
caches the latest IFS run's surface fields (~200 MB via ../../open-data/scripts/odcatalog.py),
then each click extracts the nearest gridpoint with odpoint.py --file (earthkit) and plots it with
../../earthkit/scripts/ekplot.py meteogram (earthkit-plots). Needs uv for those two.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import shutil
import subprocess
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

HERE = Path(__file__).resolve().parent
ASSETS = HERE.parent / "assets" / "webmap"
SKILLS = HERE.parent.parent
WMS = "https://eccharts.ecmwf.int/wms/"
# Public met layers (no account). Users can add CAMS composition_* layers with --layers.
DEFAULT_LAYERS = [
    "msl_public",
    "t850_public",
    "ws850_public",
    "z500_public",
    "msl_mean_public",
    "msl_spread_public",
]
PARAMS = "2t,tp,10u,10v,msl,tcc"  # what odpoint.py turns into meteogram panels
STEPS = "0-240"  # 10 days; the 00/12 UTC IFS runs reach 360 h
GRID_DEG = 0.25  # Open Data grid spacing — points in the same cell share a cache entry
LATEST_TTL_S = 600  # re-check for a newer run at most every 10 minutes
CMD_TIMEOUT_S = 900  # first download of ~200 MB can take several minutes on slow links


def create(
    outdir: Path,
    layers: list[str] | None = None,
    center=(50.0, 10.0),
    zoom: int = 4,
    token: str = "public",
) -> Path:
    if token != "public":
        raise ValueError(
            "web pages are public — only token=public may be embedded; "
            "proxy non-public layers through a server instead"
        )
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    for f in ("index.html", "app.js", "style.css"):
        shutil.copy(ASSETS / f, outdir / f)
    cfg = {
        "wms": WMS,
        "token": token,
        "layers": layers or DEFAULT_LAYERS,
        "center": list(center),
        "zoom": zoom,
    }
    (outdir / "config.json").write_text(json.dumps(cfg, indent=2))
    return outdir


def cache_key(lat: float, lon: float) -> str:
    def snap(x):
        return round(x / GRID_DEG) * GRID_DEG + 0.0  # + 0.0 avoids "-0.00"

    return f"{snap(lat):.2f}_{snap(lon):.2f}"


def sibling_scripts() -> dict[str, Path]:
    return {
        "odcatalog": SKILLS / "open-data" / "scripts" / "odcatalog.py",
        "odpoint": SKILLS / "open-data" / "scripts" / "odpoint.py",
        "ekplot": SKILLS / "earthkit" / "scripts" / "ekplot.py",
    }


def _run(cmd: list[str], **kw) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=CMD_TIMEOUT_S, **kw)
    if p.returncode != 0:
        raise RuntimeError(
            (p.stderr or p.stdout).strip().splitlines()[-1]
            if (p.stderr or p.stdout)
            else f"{cmd[0]} failed"
        )
    return p.stdout


def _latest_run_id() -> str:
    spec = importlib.util.spec_from_file_location("odcatalog", sibling_scripts()["odcatalog"])
    assert spec and spec.loader
    oc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(oc)
    run = oc.latest_run("ifs", "oper", step=int(STEPS.split("-")[1]))
    if run is None:
        raise RuntimeError("no complete ECMWF Open Data run found")
    return run.strftime("%Y%m%d%H")


class MeteogramService:
    """Caches one run's global fields, then serves per-point JSON and meteogram PNGs."""

    def __init__(self, cache_dir: Path, runner=_run, latest=None):
        self.cache = Path(cache_dir)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.run, self.latest = runner, latest or _latest_run_id
        self.scripts = sibling_scripts()
        self._lock = threading.Lock()
        self._latest = (0.0, "")

    def _run_id(self) -> str:
        t, rid = self._latest
        if time.time() - t > LATEST_TTL_S or not rid:
            rid = self.latest()
            self._latest = (time.time(), rid)
        return rid

    def _grib(self, rid: str) -> Path:
        g = self.cache / f"{rid}.grib2"
        with self._lock:  # one download per run, however many clicks arrive
            if not g.exists():
                tmp = g.with_suffix(".part")
                self.run(
                    [
                        sys.executable,
                        str(self.scripts["odcatalog"]),
                        "download",
                        "--date",
                        rid[:8],
                        "--time",
                        rid[8:],
                        "--param",
                        PARAMS,
                        "--step",
                        STEPS,
                        "-o",
                        str(tmp),
                    ]
                )
                tmp.rename(g)
                for old in self.cache.glob("*.grib2"):  # keep only the current run
                    if old != g:
                        old.unlink()
        return g

    def _json(self, lat: float, lon: float) -> Path:
        rid = self._run_id()
        j = self.cache / f"{rid}_{cache_key(lat, lon)}.json"
        if not j.exists():
            out = self.run(
                [
                    "uv",
                    "run",
                    "--quiet",
                    str(self.scripts["odpoint"]),
                    "--lat",
                    str(lat),
                    "--lon",
                    str(lon),
                    "--file",
                    str(self._grib(rid)),
                    "--json",
                ]
            )
            j.write_text(out)
        return j

    def point(self, lat: float, lon: float) -> dict:
        return json.loads(self._json(lat, lon).read_text())

    def meteogram(self, lat: float, lon: float) -> Path:
        j = self._json(lat, lon)
        png = j.with_suffix(".png")
        if not png.exists():
            self.run(
                [
                    "uv",
                    "run",
                    "--quiet",
                    str(self.scripts["ekplot"]),
                    "meteogram",
                    str(j),
                    "-o",
                    str(png),
                ]
            )
        return png


def _coords(qs: dict) -> tuple[float, float]:
    lat, lon = float(qs["lat"][0]), float(qs["lon"][0])
    if not (-90 <= lat <= 90 and -180 <= lon <= 360):
        raise ValueError("lat must be in [-90, 90] and lon in [-180, 360]")
    return lat, lon


def allowed_hosts(bind: str, port: int, extra=()) -> set[str]:
    """Host headers the server answers: loopback names, the bound address, and --allow-host."""
    names = {"127.0.0.1", "localhost", "[::1]", *extra}
    if bind not in ("0.0.0.0", "::", ""):
        names.add(bind)
    return {f"{n}:{port}" for n in names}


def make_server(outdir: Path, port: int = 8000, service=None, host: str = "127.0.0.1", allow=()):
    outdir = Path(outdir).resolve()
    service = service or MeteogramService(outdir / ".cache")

    class Handler(SimpleHTTPRequestHandler):
        def _trusted(self) -> bool:
            """Only this machine's own page may use the server: reject other Host names
            (DNS rebinding) and cross-site Origins."""
            ok_hosts = allowed_hosts(host, self.server.server_address[1], allow)
            if self.headers.get("Host", "") not in ok_hosts:
                return False
            origin = self.headers.get("Origin")
            return origin is None or origin in {f"http://{h}" for h in ok_hosts}

        def do_GET(self):
            if not self._trusted():
                return self._send(403, "text/plain", b"forbidden: local use only")
            u = urlparse(self.path)
            if not u.path.startswith("/api/"):
                return super().do_GET()
            try:
                lat, lon = _coords(parse_qs(u.query))
            except (KeyError, ValueError) as e:
                return self._send(400, "text/plain", f"bad request: {e}".encode())
            try:
                if u.path == "/api/meteogram.png":
                    return self._send(200, "image/png", service.meteogram(lat, lon).read_bytes())
                if u.path == "/api/point.json":
                    return self._send(
                        200,
                        "application/json",
                        json.dumps(service.point(lat, lon)).encode(),
                    )
            except Exception as e:  # report upstream failures to the page
                return self._send(502, "text/plain", f"meteogram failed: {e}".encode())
            return self._send(404, "text/plain", b"not found")

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            sys.stderr.write("%s\n" % (format % args))

    return ThreadingHTTPServer((host, port), functools.partial(Handler, directory=str(outdir)))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("outdir")
    c.add_argument(
        "--layers",
        help=f"comma list of WMS layer names (default {','.join(DEFAULT_LAYERS)})",
    )
    c.add_argument("--center", default="50,10", help="lat,lon")
    c.add_argument("--zoom", type=int, default=4)
    s = sub.add_parser("serve")
    s.add_argument("outdir")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument(
        "--allow-host",
        action="append",
        default=[],
        help="extra host name clients use (needed with --host 0.0.0.0)",
    )
    a = ap.parse_args(argv)
    try:
        if a.cmd == "create":
            lat, lon = (float(x) for x in a.center.split(","))
            out = create(
                Path(a.outdir),
                a.layers.split(",") if a.layers else None,
                (lat, lon),
                a.zoom,
            )
            print(
                f"created {out}/ (index.html, app.js, style.css, config.json)\n"
                f"Run: python3 {Path(__file__).resolve()} serve {out}  then open http://127.0.0.1:8000\n"
                "Opening index.html directly also works, but click meteograms need the server."
            )
            return 0
        missing = [k for k, p in sibling_scripts().items() if not p.exists()]
        if missing:
            print(
                f"warning: {missing} not found — meteograms need the open-data and earthkit skills",
                file=sys.stderr,
            )
        srv = make_server(Path(a.outdir), a.port, host=a.host, allow=a.allow_host)
        print(f"serving {a.outdir} on http://{a.host}:{srv.server_address[1]}  (Ctrl-C to stop)")
        srv.serve_forever()
    except (ValueError, OSError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
