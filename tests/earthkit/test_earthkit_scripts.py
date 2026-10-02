import json
import subprocess

import pytest

from conftest import FIXTURES, SKILLS, load_script

GRIB = FIXTURES / "ifs-sfc-5deg-20261002-00z.grib2"
SCRIPTS = SKILLS / "earthkit" / "scripts"

POINT_JSON = {
    "location": {"lat": 38.72, "lon": -9.14},
    "gridpoint": {"lat": 38.75, "lon": -9.25, "distance_km": 10.1},
    "run": "2026-10-02T00:00Z",
    "model": "ifs",
    "units": {
        "t2m_C": "degC",
        "precip_mm": "mm since previous step",
        "wind_speed_ms": "m/s",
    },
    "series": [
        {
            "step": 0,
            "valid_time": "2026-10-02T00:00Z",
            "t2m_C": 15.2,
            "precip_mm": 0.0,
            "wind_speed_ms": 2.4,
        },
        {
            "step": 6,
            "valid_time": "2026-10-02T06:00Z",
            "t2m_C": 14.0,
            "precip_mm": 1.2,
            "wind_speed_ms": 3.1,
        },
        {
            "step": 12,
            "valid_time": "2026-10-02T12:00Z",
            "t2m_C": 22.5,
            "precip_mm": 0.0,
            "wind_speed_ms": 1.0,
        },
    ],
    "attribution": {"short": "Data: © 2026 ECMWF, CC BY 4.0", "licence": "CC-BY-4.0"},
}


def uv(script, *args, timeout=600):
    return subprocess.run(
        ["uv", "run", "--quiet", str(SCRIPTS / script), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# --- importable without earthkit ---------------------------------------------------------------


def test_ekinspect_imports_without_earthkit():
    m = load_script("earthkit", "ekinspect")
    assert hasattr(m, "summarise")


def test_ekplot_meteogram_panels_from_json():
    m = load_script("earthkit", "ekplot")
    panels = m.meteogram_panels(POINT_JSON)
    assert [p["key"] for p in panels] == ["t2m_C", "precip_mm", "wind_speed_ms"]
    assert panels[1]["kind"] == "bar"
    assert panels[0]["values"] == [15.2, 14.0, 22.5]


def test_ekplot_meteogram_panels_none_becomes_nan():
    import math

    m = load_script("earthkit", "ekplot")
    pj = json.loads(json.dumps(POINT_JSON))
    pj["series"][0]["precip_mm"] = None
    p = [x for x in m.meteogram_panels(pj) if x["key"] == "precip_mm"][0]
    assert math.isnan(p["values"][0]) and p["values"][1] == 1.2


# --- earthkit-backed ---------------------------------------------------------------------------


@pytest.mark.earthkit
def test_ekinspect_json_summary():
    out = uv("ekinspect.py", GRIB, "--json")
    assert out.returncode == 0, out.stderr
    s = json.loads(out.stdout)
    assert s["fields"] == 18
    assert set(s["params"]) == {"2t", "tp", "10u", "10v", "msl", "tcc"}
    assert s["params"]["2t"]["units"] == "K"
    assert s["params"]["2t"]["steps"] == [0, 6, 12]
    assert s["base_times"] == ["2026-10-02T00:00Z"]
    assert s["valid_time_range"] == ["2026-10-02T00:00Z", "2026-10-02T12:00Z"]
    assert s["grid"]["shape"] == [37, 72]


@pytest.mark.earthkit
def test_ekplot_map_png(tmp_path):
    png = tmp_path / "map.png"
    out = uv(
        "ekplot.py",
        "map",
        GRIB,
        "--param",
        "2t",
        "--step",
        "12",
        "--units",
        "celsius",
        "--domain",
        "Europe",
        "-o",
        png,
    )
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n" and png.stat().st_size > 20_000


@pytest.mark.earthkit
def test_ekplot_map_unknown_param_lists_available(tmp_path):
    out = uv("ekplot.py", "map", GRIB, "--param", "zz", "-o", tmp_path / "x.png")
    assert out.returncode != 0 and "2t" in out.stderr


@pytest.mark.earthkit
def test_ekplot_meteogram_png(tmp_path):
    j = tmp_path / "p.json"
    j.write_text(json.dumps(POINT_JSON))
    png = tmp_path / "m.png"
    out = uv("ekplot.py", "meteogram", j, "-o", png)
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG" and png.stat().st_size > 10_000
