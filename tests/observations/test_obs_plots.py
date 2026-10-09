# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Observations drawn by ecmwf-earthkit's ekplot.py: dots on a meteogram, stations on a map."""

import json
import subprocess

import pytest
from conftest import FIXTURES, SKILLS, load_script

EKPLOT = SKILLS / "ecmwf-earthkit" / "scripts" / "ekplot.py"
POINT = {
    "run": "2026-10-02T00:00Z",
    "model": "ifs",
    "gridpoint": {"lat": 38.75, "lon": -9.25},
    "units": {"t2m_C": "degC", "wind_speed_ms": "m/s"},
    "series": [
        {
            "step": s,
            "valid_time": f"2026-10-02T{s:02d}:00Z",
            "t2m_C": 15.0 + s / 2,
            "wind_speed_ms": 3.0,
        }
        for s in (0, 6, 12)
    ],
    "attribution": {"short": "Data: © 2026 ECMWF, CC BY 4.0"},
    "observations": {
        "label": "observed (ECMWF MARS observations)",
        "series": [
            {"valid_time": "2026-10-02T00:00Z", "t2m_C": 14.2},
            {"valid_time": "2026-10-02T03:00Z", "t2m_C": 14.8, "wind_speed_ms": 2.1},
        ],
    },
}
POINTS = {
    "variable": "t2m",
    "units": "degC",
    "source": "ECMWF MARS observations",
    "points": [
        {"station": "08535", "lat": 38.72, "lon": -9.15, "value": 21.0},
        {"station": "03772", "lat": 51.48, "lon": -0.45, "value": 14.0},
    ],
}


def test_observation_points_for_a_panel():
    m = load_script("ecmwf-earthkit", "ekplot")
    assert m.observation_points(POINT, "t2m_C") == (
        ["2026-10-02T00:00", "2026-10-02T03:00"],
        [14.2, 14.8],
    )
    assert m.observation_points(POINT, "wind_speed_ms") == (["2026-10-02T03:00"], [2.1])
    assert m.observation_points({"series": []}, "t2m_C") == ([], [])


def uv(*args):
    return subprocess.run(
        ["uv", "run", "--quiet", str(EKPLOT), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=600,
    )


@pytest.mark.earthkit
def test_meteogram_with_observations_png(tmp_path):
    j = tmp_path / "p.json"
    j.write_text(json.dumps(POINT))
    out = uv("meteogram", j, "-o", tmp_path / "m.png")
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "m.png").read_bytes()[:4] == b"\x89PNG"


@pytest.mark.earthkit
def test_map_with_station_overlay_png(tmp_path):
    j = tmp_path / "stations.json"
    j.write_text(json.dumps(POINTS))
    png = tmp_path / "map.png"
    grib = FIXTURES / "ifs-sfc-5deg-20261002-00z.grib2"
    out = uv(
        "map",
        grib,
        "--param",
        "2t",
        "--step",
        "12",
        "--units",
        "celsius",
        "--domain",
        "Europe",
        "--obs",
        j,
        "-o",
        png,
    )
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG"


@pytest.mark.earthkit
def test_station_values_use_the_field_style_units():
    import types

    m = load_script("ecmwf-earthkit", "ekplot")
    drawn = {}
    style = types.SimpleNamespace(units="kelvin")
    fig = types.SimpleNamespace(
        layers=[types.SimpleNamespace(style=style)], scatter=lambda **k: drawn.update(k)
    )
    assert m.overlay_stations(fig, POINTS) == "ECMWF MARS observations"
    assert list(drawn["z"]) == pytest.approx([294.15, 287.15])
    assert list(drawn["x"]) == [-9.15, -0.45] and drawn["style"] is style
    with pytest.raises(ValueError, match="no station points"):
        m.overlay_stations(fig, {"points": []})
