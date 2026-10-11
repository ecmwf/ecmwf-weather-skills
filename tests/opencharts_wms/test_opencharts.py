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


# --- Charts drawn for one place: the ENS meteogram and the other point products ------------------

SCHEMA_METEOGRAM = json.loads((FIXTURES / "opencharts-schema-meteogram.json").read_text())
PRODUCT_METEOGRAM = json.loads((FIXTURES / "opencharts-product-meteogram.json").read_text())
EPSGRAMS = [
    "classical_10d",
    "classical_15d",
    "classical_15d_with_climate",
    "classical_plume",
    "classical_wave",
]
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _query(url: str) -> dict:
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def _fake_api(monkeypatch, schema=SCHEMA_METEOGRAM, product=PRODUCT_METEOGRAM, unavailable=()):
    """Serve recorded OpenCharts answers; a base time in `unavailable` answers as the API does
    for a run whose chart is not ready. Returns the product URLs requested."""
    asked = []

    def get_json(url):
        if "/schema/" in url:
            return schema
        asked.append(url)
        base = _query(url).get("base_time")
        if base in unavailable:
            raise RuntimeError(
                "HTTP 404: ['Product opencharts_meteogram not available --> "
                f"(base_time {base} is not available)']"
            )
        return product

    monkeypatch.setattr(oc, "_get_json", get_json)
    monkeypatch.setattr(oc, "_get_bytes", lambda url: PNG_SIGNATURE)
    return asked


def test_parse_schema_of_a_chart_for_one_place():
    o = oc.parse_schema(SCHEMA_METEOGRAM)
    assert o["point"] is True
    assert o["kinds"] == EPSGRAMS and o["kind_default"] == "classical_10d"
    assert o["base_times"][:2] == ["2026-10-11T00:00:00Z", "2026-10-10T12:00:00Z"]
    assert o["steps"] == [] and o["projections"] == []


def test_maps_are_not_drawn_for_a_place():
    o = oc.parse_schema(SCHEMA)
    assert o["point"] is False and o["kinds"] == [] and o["kind_default"] is None


def test_kind_names_are_the_ecmwf_mcp_names():
    names = [oc.kind_name(k) for k in EPSGRAMS]
    assert names == ["10-day", "15-day", "15-day-with-climate", "plume", "wave"]
    assert oc.kind_name("aifs_ens_classical_plume") == "plume"
    assert oc.kind_name("visibility_meteogram_public") == "visibility_meteogram_public"


def test_resolve_kind_from_a_name_or_the_api_value():
    assert oc.resolve_kind("10-day", EPSGRAMS, "opencharts_meteogram") == "classical_10d"
    assert oc.resolve_kind("15-day", EPSGRAMS, "opencharts_meteogram") == "classical_15d"
    with_climate = oc.resolve_kind("15-day-with-climate", EPSGRAMS, "opencharts_meteogram")
    assert with_climate == "classical_15d_with_climate"
    assert oc.resolve_kind("classical_plume", EPSGRAMS, "opencharts_meteogram") == "classical_plume"
    aifs = ["aifs_ens_classical_10d", "aifs_ens_classical_plume"]
    assert oc.resolve_kind("plume", aifs, "aifs_ens_opencharts_meteogram") == aifs[1]
    visibility = ["visibility_meteogram_aviation", "visibility_meteogram_public"]
    assert oc.resolve_kind("public", visibility, "visibility_meteogram") == visibility[1]


def test_a_wrong_kind_lists_the_kinds_of_the_product():
    aifs = ["aifs_ens_classical_10d", "aifs_ens_classical_plume"]
    with pytest.raises(ValueError, match="choose from: 10-day, plume"):
        oc.resolve_kind("wave", aifs, "aifs_ens_opencharts_meteogram")
    with pytest.raises(ValueError, match="drop --kind"):
        oc.resolve_kind("10-day", [], "opencharts_ptype_meteogram")


def test_check_place():
    assert oc.check_place("opencharts_meteogram", True, 44.49, 11.34) == (44.49, 11.34)
    # OpenCharts reads longitudes 180..360 as west (200 is drawn at 160 W).
    assert oc.check_place("opencharts_meteogram", True, -33.9, 358.5) == (-33.9, 358.5)
    assert oc.check_place("medium-2t-wind", False, None, None) is None


@pytest.mark.parametrize(
    "lat, lon, message",
    [
        (95, 0, "latitude 95 must be within -90..90"),
        (-90.5, 0, "latitude -90.5 must be within -90..90"),
        (0, -181, "longitude -181 must be within -180..360"),
        (0, 361, "longitude 361 must be within -180..360"),
        (float("nan"), 0, "latitude nan must be within -90..90"),
    ],
)
def test_check_place_rejects_coordinates_out_of_range(lat, lon, message):
    with pytest.raises(ValueError, match=message):
        oc.check_place("opencharts_meteogram", True, lat, lon)


def test_check_place_needs_both_coordinates_for_a_chart_of_one_place():
    for lat, lon in ((44.49, None), (None, 11.34), (None, None)):
        with pytest.raises(ValueError, match="add --lat LAT --lon LON"):
            oc.check_place("opencharts_meteogram", True, lat, lon)


def test_check_place_refuses_coordinates_for_a_map():
    with pytest.raises(ValueError, match="not drawn for a place: drop --lat/--lon"):
        oc.check_place("medium-2t-wind", False, 44.49, 11.34)


def test_product_url_for_a_place():
    url = oc.product_url(
        "opencharts_meteogram",
        base_time="2026-10-11T00:00:00Z",
        lat=44.49,
        lon=11.34,
        epsgram="classical_10d",
    )
    assert urlparse(url).path == "/opencharts-api/v1/products/opencharts_meteogram/"
    assert _query(url) == {
        "base_time": "2026-10-11T00:00:00Z",
        "lat": "44.49",
        "lon": "11.34",
        "epsgram": "classical_10d",
        "format": "png",
    }


def test_cli_get_meteogram_for_a_place(tmp_path, monkeypatch, capsys):
    asked = _fake_api(monkeypatch)
    png = tmp_path / "bologna.png"
    args = ["get", "opencharts_meteogram", "--lat", "44.49", "--lon", "11.34", "-o", str(png)]
    assert oc.main(args) == 0
    # The newest run, the default layout and the place are always sent: without lat/lon the
    # API draws a default point instead of the place. A meteogram has no projection.
    assert [_query(u) for u in asked] == [
        {
            "base_time": "2026-10-11T00:00:00Z",
            "lat": "44.49",
            "lon": "11.34",
            "epsgram": "classical_10d",
            "format": "png",
        }
    ]
    assert png.read_bytes() == PNG_SIGNATURE
    out = capsys.readouterr().out.splitlines()
    assert out[0] == f"saved {png}"
    assert out[1].startswith("ENS Meteograms (10-day) for 44.49°N 11.34°E")
    assert "gridpoint" in out[1]
    assert out[2] == "Base time: Sun 11 Oct 2026 00 UTC"
    assert out[3] == f"Attribution: {oc.attribution()}" and "CC BY 4.0" in out[3]


def test_cli_get_meteogram_kind_and_base_time(tmp_path, monkeypatch, capsys):
    asked = _fake_api(monkeypatch)
    args = ["get", "opencharts_meteogram", "--lat", "-33.92", "--lon", "18.42", "--kind", "plume"]
    args += ["--base-time", "2026-10-10T12:00:00Z", "-o", str(tmp_path / "m.png")]
    assert oc.main(args) == 0
    assert len(asked) == 1
    q = _query(asked[0])
    assert q["epsgram"] == "classical_plume" and q["base_time"] == "2026-10-10T12:00:00Z"
    out = capsys.readouterr().out
    assert "ENS Meteograms (plume) for 33.92°S 18.42°E" in out
    assert "Base time: Sat 10 Oct 2026 12 UTC" in out


def test_cli_get_meteogram_of_the_previous_run_names_that_run(tmp_path, monkeypatch, capsys):
    asked = _fake_api(monkeypatch, unavailable={"2026-10-11T00:00:00Z"})
    args = ["get", "opencharts_meteogram", "--lat", "44.49", "--lon", "11.34"]
    assert oc.main([*args, "-o", str(tmp_path / "m.png")]) == 0
    assert [_query(u)["base_time"] for u in asked] == [
        "2026-10-11T00:00:00Z",
        "2026-10-10T12:00:00Z",
    ]
    assert "Base time: Sat 10 Oct 2026 12 UTC" in capsys.readouterr().out


@pytest.mark.parametrize(
    "extra, message",
    [
        ([], "opencharts_meteogram is drawn for one place: add --lat LAT --lon LON"),
        (["--lat", "44.49"], "add --lat LAT --lon LON"),
        (["--lat", "95", "--lon", "11.34"], "latitude 95.0 must be within -90..90"),
        (["--lat", "44.49", "--lon", "11.34", "--kind", "20-day"], "choose from: 10-day, 15-day"),
        (
            ["--lat", "44.49", "--lon", "11.34", "--projection", "opencharts_europe"],
            "opencharts_meteogram takes no --projection",
        ),
        (
            ["--lat", "44.49", "--lon", "11.34", "--step", "48"],
            "opencharts_meteogram takes no --step",
        ),
        (
            ["--lat", "44.49", "--lon", "11.34", "--valid-time", "2026-10-13T00:00:00Z"],
            "opencharts_meteogram takes no --valid-time",
        ),
    ],
)
def test_cli_get_meteogram_refuses_what_would_draw_the_wrong_chart(
    extra, message, tmp_path, monkeypatch, capsys
):
    # Without lat/lon the API answers HTTP 200 with a default point's meteogram, with lat=95
    # an HTTP 500 that names neither, and with a step "step 48 is not available" (it has
    # none): check before asking.
    asked = _fake_api(monkeypatch)
    png = tmp_path / "m.png"
    assert oc.main(["get", "opencharts_meteogram", *extra, "-o", str(png)]) == 2
    assert message in capsys.readouterr().err
    assert asked == [] and not png.exists()


@pytest.mark.parametrize(
    "extra, message",
    [
        (["--lat", "44.49", "--lon", "11.34"], "medium-2t-wind is not drawn for a place"),
        (["--kind", "plume"], "medium-2t-wind has one layout: drop --kind"),
    ],
)
def test_cli_get_map_refuses_options_of_charts_for_one_place(
    extra, message, tmp_path, monkeypatch, capsys
):
    # Maps ignore lat/lon silently (HTTP 200, the usual map), so say so instead.
    asked = _fake_api(monkeypatch, schema=SCHEMA, product=PRODUCT)
    args = ["get", "medium-2t-wind", "--step", "48", *extra, "-o", str(tmp_path / "t.png")]
    assert oc.main(args) == 2
    assert message in capsys.readouterr().err and asked == []


SCHEMA_EFI = json.loads((FIXTURES / "opencharts-schema-efi-cdf-meteogram.json").read_text())
PRODUCT_EFI = json.loads((FIXTURES / "opencharts-product-efi-cdf-meteogram.json").read_text())


def test_cli_get_chart_for_one_place_with_a_step(tmp_path, monkeypatch, capsys):
    # EFI/CDF and vertical profiles take a place and a step, and have no layouts. The EFI/CDF
    # chart of step 48 covers the 24 h up to it, so the step is printed, not a valid time.
    asked = _fake_api(monkeypatch, schema=SCHEMA_EFI, product=PRODUCT_EFI)
    args = ["get", "opencharts_efi-cdf-meteogram", "--lat", "44.49", "--lon", "11.34"]
    assert oc.main([*args, "--step", "48", "-o", str(tmp_path / "efi.png")]) == 0
    assert [_query(u) for u in asked] == [
        {
            "base_time": "2026-10-11T00:00:00Z",
            "step": "48",
            "lat": "44.49",
            "lon": "11.34",
            "format": "png",
        }
    ]
    out = capsys.readouterr().out
    assert "EFI/CDF for 44.49°N 11.34°E" in out
    assert "Base time: Sun 11 Oct 2026 00 UTC, step 48 h" in out


def test_run_label_writes_times_as_opencharts_does():
    assert oc.run_label("2026-10-11T00:00:00Z") == "Sun 11 Oct 2026 00 UTC"
    assert oc.run_label("2026-10-11 00") == "2026-10-11 00"  # another format: as given


def test_cli_get_map_keeps_the_europe_projection_by_default(tmp_path, monkeypatch, capsys):
    asked = _fake_api(monkeypatch, schema=SCHEMA, product=PRODUCT)
    assert oc.main(["get", "medium-2t-wind", "--step", "48", "-o", str(tmp_path / "t.png")]) == 0
    q = _query(asked[0])
    assert q["projection"] == "opencharts_europe" and q["step"] == "48"
    assert not {"lat", "lon", "epsgram"} & set(q)
    out = capsys.readouterr().out.splitlines()
    assert out[1] == PRODUCT["data"]["attributes"]["description"]


def test_options_of_a_meteogram_name_the_place_and_kinds(monkeypatch, capsys):
    monkeypatch.setattr(oc, "_get_json", lambda url: SCHEMA_METEOGRAM)
    assert oc.main(["options", "opencharts_meteogram"]) == 0
    out = capsys.readouterr().out
    assert "place      : --lat LAT --lon LON (required)" in out
    kinds = "10-day, 15-day, 15-day-with-climate, plume, wave (--kind; default 10-day)"
    assert f"kinds      : {kinds}" in out


def test_options_of_a_map_name_no_place_or_kinds(monkeypatch, capsys):
    monkeypatch.setattr(oc, "_get_json", lambda url: SCHEMA)
    assert oc.main(["options", "medium-2t-wind"]) == 0
    out = capsys.readouterr().out
    assert "place" not in out and "kinds" not in out


@pytest.mark.live
def test_live_get_meteogram_for_a_point(tmp_path):
    png = tmp_path / "newbury.png"
    args = ["get", "opencharts_meteogram", "--lat", "51.40", "--lon", "-1.32", "--kind", "10-day"]
    out = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "-o", str(png)],
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:8] == PNG_SIGNATURE
    assert "Base time: " in out.stdout and "CC BY 4.0" in out.stdout
