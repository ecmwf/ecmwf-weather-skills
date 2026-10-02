import json
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import SKILLS, load_script

wm = load_script("opencharts-wms", "webmap")
ASSETS = SKILLS / "opencharts-wms" / "assets" / "webmap"


def test_create_writes_page_assets_and_config(tmp_path):
    out = tmp_path / "webmap"
    wm.create(out, layers=["msl_public", "t850_public"], center=(50.0, 10.0), zoom=4)
    for f in ("index.html", "app.js", "style.css", "config.json"):
        assert (out / f).exists(), f
    cfg = json.loads((out / "config.json").read_text())
    assert cfg["layers"] == ["msl_public", "t850_public"]
    assert cfg["wms"] == "https://eccharts.ecmwf.int/wms/" and cfg["token"] == "public"
    html = (out / "index.html").read_text()
    assert "leaflet" in html.lower() and "app.js" in html
    assert "ECMWF" in (out / "app.js").read_text()


def test_create_refuses_private_token_in_page(tmp_path):
    with pytest.raises(ValueError, match="public"):
        wm.create(tmp_path / "w", token="secret")


def test_cache_key_rounds_to_grid():
    assert wm.cache_key(51.4543, -0.9781) == "51.50_-1.00"
    assert wm.cache_key(38.72, -9.14) == "38.75_-9.25"


def test_sibling_scripts_found():
    s = wm.sibling_scripts()
    assert s["odcatalog"].name == "odcatalog.py" and s["odcatalog"].exists()
    assert s["odpoint"].exists() and s["ekplot"].exists()


def test_service_commands_cache_run_then_extract(tmp_path):
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd)
        if "download" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"GRIB")
        if "--json" in cmd and any("odpoint" in c for c in cmd):
            return json.dumps({"series": [{"step": 0}], "run": "2026-10-02T00:00Z"})
        if "meteogram" in cmd:
            Path(cmd[cmd.index("-o") + 1]).write_bytes(b"\x89PNG")
        return ""

    svc = wm.MeteogramService(
        tmp_path / ".cache", runner=runner, latest=lambda: "2026100200"
    )
    png = svc.meteogram(38.72, -9.14)
    assert png.read_bytes() == b"\x89PNG"
    flat = [" ".join(map(str, c)) for c in calls]
    assert any("odcatalog.py download" in c and "--step 0-240" in c for c in flat)
    assert any("odpoint.py" in c and "--file" in c for c in flat)
    n = len(calls)
    svc.meteogram(38.72, -9.14)  # cached point: no new work
    assert len(calls) == n
    svc.meteogram(40.0, -8.0)  # new point: no new download, only extract + plot
    assert not any("download" in " ".join(map(str, c)) for c in calls[n:])


def test_server_routes(tmp_path):
    out = tmp_path / "webmap"
    wm.create(out)

    class FakeSvc:
        def meteogram(self, lat, lon):
            p = tmp_path / "m.png"
            p.write_bytes(b"\x89PNGfake")
            return p

        def point(self, lat, lon):
            return {"series": [], "lat": lat}

    srv = wm.make_server(out, port=0, service=FakeSvc())
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        assert b"leaflet" in urllib.request.urlopen(base + "/").read().lower()
        r = urllib.request.urlopen(base + "/api/meteogram.png?lat=38.7&lon=-9.1")
        assert r.headers["Content-Type"] == "image/png" and r.read().startswith(
            b"\x89PNG"
        )
        assert (
            json.loads(
                urllib.request.urlopen(base + "/api/point.json?lat=1&lon=2").read()
            )["lat"]
            == 1
        )
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/api/meteogram.png?lat=999&lon=0")
        assert e.value.code == 400
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(base + "/../../etc/passwd")
        assert e.value.code in (400, 403, 404)
    finally:
        srv.shutdown()


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_app_js_time_parsing_with_node():
    js = (ASSETS / "app.js").read_text()
    script = (
        js
        + """
;const out = {
  times: expandTime('2026-10-03T00:00:00Z,2026-10-03T06:00:00Z/2026-10-03T18:00:00Z/PT6H'),
  closest: closestTime(['2026-10-03T06:00:00Z','2026-10-03T12:00:00Z'], new Date('2026-10-03T10:00:00Z')),
};
console.log(JSON.stringify(out));
"""
    )
    res = subprocess.run(
        ["node", "-e", script], capture_output=True, text=True, timeout=30
    )
    assert res.returncode == 0, res.stderr
    out = json.loads(res.stdout)
    assert out["times"] == [
        "2026-10-03T00:00:00Z",
        "2026-10-03T06:00:00Z",
        "2026-10-03T12:00:00Z",
        "2026-10-03T18:00:00Z",
    ]
    assert out["closest"] == "2026-10-03T12:00:00Z"
