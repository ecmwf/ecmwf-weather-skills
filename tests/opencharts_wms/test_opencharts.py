# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from conftest import FIXTURES, SKILLS, load_script

oc = load_script("ecmwf-opencharts-wms", "opencharts")
SEARCH = json.loads((FIXTURES / "opencharts-search-trimmed.json").read_text())
SCHEMA = json.loads((FIXTURES / "opencharts-schema.json").read_text())
PRODUCT = json.loads((FIXTURES / "opencharts-product.json").read_text())
SCRIPT = SKILLS / "ecmwf-opencharts-wms" / "scripts" / "opencharts.py"


def test_search_matches_name_and_title_all_words():
    hits = [p["name"] for p in oc.search(SEARCH["results"], "2 m temperature wind")]
    assert hits[0] == "medium-2t-wind"
    assert "aifs_single_medium-2t-wind" in hits
    assert "medium-mslp-wind850" not in hits


def test_search_model_filter():
    hits = [p["name"] for p in oc.search(SEARCH["results"], "temperature", model="aifs")]
    assert hits and all(h.startswith("aifs") for h in hits)
    hits = [p["name"] for p in oc.search(SEARCH["results"], "temperature", model="ifs")]
    assert hits and not any(h.startswith("aifs") for h in hits)


def test_parse_schema_options():
    o = oc.parse_schema(SCHEMA)
    assert o["base_times"][0] == "2026-10-02T12:00:00Z"
    assert 48 in o["steps"] and "opencharts_europe" in o["projections"]
    assert o["formats"] == ["png", "pdf"]
    assert o["layer_names"] == ["2t", "mx2t", "mn2t"]


def test_product_url():
    url = oc.product_url(
        "medium-2t-wind",
        base_time="2026-10-02T12:00:00Z",
        step=48,
        projection="opencharts_europe",
    )
    u = urlparse(url)
    assert u.path == "/opencharts-api/v1/products/medium-2t-wind/"
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    assert q == {
        "base_time": "2026-10-02T12:00:00Z",
        "step": "48",
        "projection": "opencharts_europe",
        "format": "png",
    }


def test_image_link_and_meta():
    link, meta = oc.image_link(PRODUCT)
    assert link.startswith("https://charts.ecmwf.int/content/") and link.endswith(".png")
    assert meta["licence"] == "CC-BY-4.0"


def test_image_link_error_message():
    with pytest.raises(RuntimeError, match="step"):
        oc.image_link({"error": "Invalid step"})


def test_validate_choice_lists_alternatives():
    with pytest.raises(ValueError, match="opencharts_europe"):
        oc.validate_choice("projection", "europe", ["opencharts_europe", "opencharts_global"])


@pytest.mark.live
def test_live_get_chart(tmp_path):
    png = tmp_path / "c.png"
    out = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "get",
            "medium-2t-wind",
            "--step",
            "48",
            "--projection",
            "opencharts_europe",
            "-o",
            str(png),
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG" and "CC BY 4.0" in out.stdout


def test_fetch_product_falls_back_to_a_run_that_has_the_step():
    calls = []

    def get(url):
        calls.append(url)
        if "base_time=2026-10-03T12" in url:
            raise RuntimeError("HTTP 404: step 48 for this base_time is not available")
        return {"data": {"link": {"href": "https://charts.ecmwf.int/content/x.png"}}}

    resp, base = oc.fetch_product(
        "medium-2t-wind",
        ["2026-10-03T12:00:00Z", "2026-10-03T00:00:00Z"],
        step=48,
        projection="opencharts_europe",
        get=get,
    )
    assert base == "2026-10-03T00:00:00Z" and len(calls) == 2
    assert resp["data"]["link"]["href"].endswith(".png")


def test_fetch_product_does_not_hide_other_errors():
    def get(url):
        raise RuntimeError("HTTP 400: projection not supported")

    with pytest.raises(RuntimeError, match="projection"):
        oc.fetch_product(
            "p", ["2026-10-03T12:00:00Z", "2026-10-03T00:00:00Z"], step=48, projection="x", get=get
        )


def test_image_link_must_be_https():
    with pytest.raises(RuntimeError, match="https"):
        oc.image_link({"data": {"link": {"href": "http://charts.ecmwf.int/content/x.png"}}})


# --- CAMS charts carry the Copernicus credit -----------------------------------------------------

PRODUCT_CAMS = json.loads((FIXTURES / "opencharts-product-cams.json").read_text())
SCHEMA_CAMS = json.loads((FIXTURES / "opencharts-schema-cams.json").read_text())
SCHEMA_POINT = json.loads((FIXTURES / "opencharts-schema-point-graph.json").read_text())
YEAR = datetime.now(timezone.utc).year
CAMS_CREDIT = (
    f"Generated using Copernicus Atmosphere Monitoring Service information {YEAR}. Neither the "
    "European Commission nor ECMWF is responsible for any use that may be made of the Copernicus "
    "information or data it contains."
)


def test_cams_products_are_recognised_from_their_metadata():
    assert PRODUCT_CAMS["template"] == "cams" and oc.is_cams("aerosol-forecasts", PRODUCT_CAMS)
    layers = oc.parse_schema(SCHEMA_CAMS)["layer_names"]
    assert oc.is_cams("aerosol-forecasts", {"template": "ecmwf"}, layers=layers)
    assert oc.is_cams("classical_cams_uvindex", {"template": "ecmwf"}, "CAMS uv index graph")
    assert not oc.is_cams("medium-2t-wind", PRODUCT, "2 m temperature and 10 m wind")
    assert not oc.is_cams("medium-2t-wind", PRODUCT, layers=["2t", "mx2t", "mn2t"])


def test_cams_charts_carry_the_copernicus_credit():
    assert oc.attribution(cams=True) == CAMS_CREDIT
    ecmwf = oc.attribution()
    assert "CC BY 4.0" in ecmwf and "Copernicus" not in ecmwf


def test_cli_get_cams_chart_ends_with_the_copernicus_credit(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        oc, "_get_json", lambda url: SCHEMA_CAMS if "/schema/" in url else PRODUCT_CAMS
    )
    monkeypatch.setattr(oc, "_get_bytes", lambda url: b"\x89PNG\r\n\x1a\n")
    args = ["get", "aerosol-forecasts", "--step", "48", "--projection", "classical_europe"]
    assert oc.main([*args, "-o", str(tmp_path / "aod.png")]) == 0
    out = capsys.readouterr().out
    assert f"Attribution: {CAMS_CREDIT}" in out and "CC BY" not in out


def test_cli_get_ecmwf_chart_keeps_the_ecmwf_line(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(oc, "_get_json", lambda url: SCHEMA if "/schema/" in url else PRODUCT)
    monkeypatch.setattr(oc, "_get_bytes", lambda url: b"\x89PNG\r\n\x1a\n")
    assert oc.main(["get", "medium-2t-wind", "--step", "48", "-o", str(tmp_path / "t.png")]) == 0
    out = capsys.readouterr().out
    assert "CC BY 4.0" in out and "Copernicus" not in out


def test_options_of_a_chart_without_steps(monkeypatch, capsys):
    # classical_cams_uvindex is a point graph: base times and a place, no steps or projections.
    monkeypatch.setattr(oc, "_get_json", lambda url: SCHEMA_POINT)
    assert oc.main(["options", "classical_cams_uvindex"]) == 0
    assert "steps      : none" in capsys.readouterr().out


@pytest.mark.live
def test_live_get_cams_chart(tmp_path):
    png = tmp_path / "aod.png"
    args = ["get", "aerosol-forecasts", "--projection", "classical_europe", "--step", "24"]
    out = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "-o", str(png)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG"
    assert "Generated using Copernicus Atmosphere Monitoring Service information" in out.stdout
