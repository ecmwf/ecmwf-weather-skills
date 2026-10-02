import json
import subprocess
import sys
from urllib.parse import parse_qs, urlparse

import pytest

from conftest import FIXTURES, SKILLS, load_script

wms = load_script("opencharts-wms", "wms")
CAPS = (FIXTURES / "wms-capabilities-trimmed.xml").read_text()
SCRIPT = SKILLS / "opencharts-wms" / "scripts" / "wms.py"


def q(url):
    return {k.lower(): v[0] for k, v in parse_qs(urlparse(url).query).items()}


# --- capabilities ----------------------------------------------------------------------------


def test_parse_capabilities_layers():
    layers = {l["name"]: l for l in wms.parse_capabilities(CAPS)}
    assert set(layers) == {
        "msl_public",
        "t850_public",
        "background",
        "composition_pm2p5",
        "z500_mean_public",
    }
    msl = layers["msl_public"]
    assert msl["title"] == "Mean sea level pressure (Public)"
    assert msl["queryable"] is True
    assert msl["styles"][0]["name"] == "ct_blk_i5_t2"
    assert msl["time"]["default"] == "2026-10-02T12:00:00Z"
    assert layers["background"]["time"] is None


def test_expand_time_mixed_list_and_intervals():
    text = (
        "2026-09-27T12:00:00Z,2026-09-27T18:00:00Z/2026-09-28T06:00:00Z/PT6H,"
        "2026-10-09T00:00:00Z/2026-10-10T00:00:00Z/PT12H"
    )
    times = wms.expand_time(text)
    assert times == [
        "2026-09-27T12:00:00Z",
        "2026-09-27T18:00:00Z",
        "2026-09-28T00:00:00Z",
        "2026-09-28T06:00:00Z",
        "2026-10-09T00:00:00Z",
        "2026-10-09T12:00:00Z",
        "2026-10-10T00:00:00Z",
    ]


def test_layer_summary_reports_forecast_range():
    msl = [l for l in wms.parse_capabilities(CAPS) if l["name"] == "msl_public"][0]
    s = wms.summarise(msl)
    assert s["first_time"] < s["default_time"] <= s["last_time"]
    assert s["forecast_hours_ahead"] > 200


def test_filter_layers():
    layers = wms.parse_capabilities(CAPS)
    assert [l["name"] for l in wms.filter_layers(layers, "pressure")] == ["msl_public"]
    assert all("public" in l["name"] for l in wms.filter_layers(layers, public=True))


def test_closest_time():
    times = ["2026-10-03T06:00:00Z", "2026-10-03T12:00:00Z", "2026-10-03T18:00:00Z"]
    assert wms.closest_time(times, "2026-10-03T13:00:00Z") == "2026-10-03T12:00:00Z"


# --- GetMap ------------------------------------------------------------------------------------


def test_getmap_url_epsg3857_converts_lonlat_bbox():
    url = wms.getmap_url(
        "msl_public",
        bbox=(-10, 35, 30, 60),
        width=800,
        height=600,
        time="2026-10-03T12:00:00Z",
    )
    p = q(url)
    assert url.startswith("https://eccharts.ecmwf.int/wms/?")
    assert (
        p["token"] == "public" and p["request"] == "GetMap" and p["version"] == "1.3.0"
    )
    assert p["crs"] == "EPSG:3857" and p["layers"] == "msl_public"
    minx, miny, maxx, maxy = map(float, p["bbox"].split(","))
    assert round(minx) == -1113195 and round(maxx) == 3339585
    assert 4163881 < miny < 4163882 and maxy > miny
    assert p["time"] == "2026-10-03T12:00:00Z" and p["transparent"] == "true"


def test_getmap_url_epsg4326_uses_lat_lon_axis_order():
    p = q(wms.getmap_url("msl_public", bbox=(-10, 35, 30, 60), crs="EPSG:4326"))
    assert p["bbox"] == "35,-10,60,30"


def test_static_layers_never_get_time():
    p = q(
        wms.getmap_url(
            "background", bbox=(-10, 35, 30, 60), time="2026-10-03T12:00:00Z"
        )
    )
    assert "time" not in p


def test_private_token_is_redacted_for_display():
    url = wms.getmap_url("msl", bbox=(0, 0, 1, 1), token="abcdef123456")
    assert "abcdef123456" in url
    assert "abcdef123456" not in wms.redact(url) and "token=***" in wms.redact(url)
    assert (
        wms.redact(wms.getmap_url("msl_public", bbox=(0, 0, 1, 1))).count(
            "token=public"
        )
        == 1
    )


def test_legend_and_featureinfo_urls():
    assert "request=GetLegend" in wms.legend_url("t850_public", "sh_all")
    p = q(wms.featureinfo_url("msl_public", lat=45, lon=0, time="2026-10-03T12:00:00Z"))
    assert p["request"] == "GetFeatureInfo" and p["info_format"] == "application/json"
    assert p["query_layers"] == "msl_public" and p["i"] == p["j"]


def test_exception_detection():
    body = b"<?xml version='1.0'?><ServiceExceptionReport><ServiceException code=\"LayerNotDefined\"><![CDATA[\nlayer 'x' not found\n]]></ServiceException></ServiceExceptionReport>"
    assert (
        wms.service_exception("text/xml", body)
        == "LayerNotDefined: layer 'x' not found"
    )
    assert wms.service_exception("image/png", b"\x89PNG") is None


def test_parse_featureinfo():
    body = '{ "Probes": [ { "Name": "msl_public", "Grid point latitude": 45.0264, "Grid point longitude": 0, "Value": { "Unit": "Pa", "Data": 102730 } } ]}'
    assert wms.parse_featureinfo(body) == [
        {"layer": "msl_public", "value": 102730, "unit": "Pa", "lat": 45.0264, "lon": 0}
    ]


# --- CLI ---------------------------------------------------------------------------------------


def cli(*args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_cli_layers_from_file_json():
    out = cli(
        "layers",
        "--caps",
        FIXTURES / "wms-capabilities-trimmed.xml",
        "--public",
        "--json",
    )
    assert out.returncode == 0, out.stderr
    names = [l["name"] for l in json.loads(out.stdout)["layers"]]
    assert names == ["t850_public", "z500_mean_public", "msl_public"]


def test_cli_url_only():
    out = cli("getmap", "--layer", "msl_public", "--bbox", "-10,35,30,60", "--url-only")
    assert out.returncode == 0, out.stderr
    assert "request=GetMap" in out.stdout


@pytest.mark.live
def test_live_getmap_png(tmp_path):
    png = tmp_path / "m.png"
    out = cli("getmap", "--layer", "msl_public", "--bbox", "-10,35,30,60", "-o", png)
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG"


@pytest.mark.live
def test_live_bad_layer_reports_exception(tmp_path):
    out = cli(
        "getmap", "--layer", "nope", "--bbox", "-10,35,30,60", "-o", tmp_path / "x.png"
    )
    assert out.returncode != 0 and "LayerNotDefined" in out.stderr
