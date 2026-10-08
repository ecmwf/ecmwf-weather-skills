# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess

import pytest
from conftest import FIXTURES, SKILLS, load_script

GRIB = FIXTURES / "ifs-sfc-5deg-20261002-00z.grib2"
SCRIPTS = SKILLS / "ecmwf-earthkit" / "scripts"

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
    m = load_script("ecmwf-earthkit", "ekinspect")
    assert hasattr(m, "summarise")


def test_ekplot_meteogram_panels_from_json():
    m = load_script("ecmwf-earthkit", "ekplot")
    panels = m.meteogram_panels(POINT_JSON)
    assert [p["key"] for p in panels] == ["t2m_C", "precip_mm", "wind_speed_ms"]
    assert panels[1]["kind"] == "bar"
    assert panels[0]["values"] == [15.2, 14.0, 22.5]


def test_ekplot_meteogram_panels_none_becomes_nan():
    import math

    m = load_script("ecmwf-earthkit", "ekplot")
    pj = json.loads(json.dumps(POINT_JSON))
    pj["series"][0]["precip_mm"] = None
    p = next(x for x in m.meteogram_panels(pj) if x["key"] == "precip_mm")
    assert math.isnan(p["values"][0]) and p["values"][1] == 1.2


ENS_JSON = {
    "run": "2026-10-02T12:00Z",
    "model": "ifs-ens",
    "gridpoint": {"lat": 51.42, "lon": -0.98},
    "units": {"t2m_C": "degC", "precip_mm": "mm since previous step"},
    "series": [
        {
            "step": s,
            "valid_time": f"2026-10-02T{12 + s:02d}:00Z",
            "t2m_C": 15.0 + s,
            "t2m_C_p10": 14.0 + s,
            "t2m_C_p50": 15.0 + s,
            "t2m_C_p90": 16.5 + s,
            "precip_mm": 0.1,
            "precip_mm_p10": 0.0,
            "precip_mm_p50": 0.1,
            "precip_mm_p90": 0.8,
            "members": 50,
        }
        for s in range(0, 12, 3)
    ],
}


def test_ekplot_meteogram_panels_ensemble_band():
    m = load_script("ecmwf-earthkit", "ekplot")
    p = {x["key"]: x for x in m.meteogram_panels(ENS_JSON)}
    assert p["t2m_C"]["band"] == ([14.0, 17.0, 20.0, 23.0], [16.5, 19.5, 22.5, 25.5])
    assert "band" in p["precip_mm"]
    assert "band" not in {x["key"]: x for x in m.meteogram_panels(POINT_JSON)}["t2m_C"]


def test_ekplot_attribution_comes_from_the_data():
    m = load_script("ecmwf-earthkit", "ekplot")
    assert (
        m.attribution_for({"attribution": {"short": "Data: © 2026 ECMWF"}}) == "Data: © 2026 ECMWF"
    )
    assert "CC BY 4.0" in m.attribution_for({})  # Open Data default


@pytest.mark.earthkit
def test_ekplot_meteogram_ensemble_png(tmp_path):
    j = tmp_path / "e.json"
    j.write_text(json.dumps(ENS_JSON))
    png = tmp_path / "e.png"
    out = uv("ekplot.py", "meteogram", j, "-o", png)
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG"


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


MIXED = {
    "run": "2026-10-03T00:00Z",
    "model": "ifs",
    "gridpoint": {"lat": 51.5, "lon": -1.0},
    "units": {"t2m_C": "degC", "precip_mm": "mm since previous step"},
    "series": [
        {"step": s, "valid_time": f"2026-10-03T{s:02d}:00Z", "t2m_C": 10.0 + s, "precip_mm": mm}
        for s, mm in ((0, 0.0), (1, 1.0), (2, 1.0), (3, 1.0), (6, 3.0), (12, 6.0))
    ],
}


@pytest.mark.earthkit
def test_precipitation_is_a_rate_over_each_interval():
    m = load_script("ecmwf-earthkit", "ekplot")
    bars = m.precip_bars(MIXED)
    assert bars["widths_h"] == [1, 1, 1, 3, 6]
    assert bars["rates"] == [1.0, 1.0, 1.0, 1.0, 1.0]  # 3 mm in 3 h == 1 mm in 1 h
    assert bars["starts"][0] == "2026-10-03T00:00" and bars["starts"][-1] == "2026-10-03T06:00"
    p = {x["key"]: x for x in m.meteogram_panels(MIXED)}
    assert "mm/h" in p["precip_mm"]["title"] + p["precip_mm"]["units"]


@pytest.mark.earthkit
def test_precipitation_rate_band_for_ensembles():
    m = load_script("ecmwf-earthkit", "ekplot")
    bars = m.precip_bars(ENS_JSON)
    assert bars["band"] is not None and len(bars["band"][0]) == len(bars["rates"])


@pytest.mark.earthkit
def test_meteogram_panels_share_one_time_axis(tmp_path):
    m = load_script("ecmwf-earthkit", "ekplot")
    fig = m.plot_meteogram(MIXED, str(tmp_path / "m.png"))
    axes = [a for a in fig.axes if a.get_title(loc="left")]
    assert len({tuple(round(v, 6) for v in a.get_xlim()) for a in axes}) == 1
    labelled = [
        a for a in axes if any(t.get_visible() and t.get_text() for t in a.get_xticklabels())
    ]
    assert labelled == [axes[-1]]  # dates only under the bottom panel
    precip = next(a for a in axes if "Precipitation" in a.get_title(loc="left"))
    widths = sorted({round(p.get_width() * 24, 3) for p in precip.patches})
    assert widths == [1.0, 3.0, 6.0]  # bar width = interval length


@pytest.mark.earthkit
def test_meteogram_local_time_axis(tmp_path):
    m = load_script("ecmwf-earthkit", "ekplot")
    pj = {**MIXED, "timezone": "Europe/London"}
    fig = m.plot_meteogram(pj, str(tmp_path / "m.png"))
    assert "Europe/London" in fig.axes[-1].get_xlabel()


ENS = FIXTURES / "ifs-ens-2t-5members-5deg.grib2"


@pytest.mark.earthkit
def test_ens_stats_mean_and_spread_maps(tmp_path):
    out = uv(
        "ekplot.py",
        "ens-stats",
        ENS,
        "--param",
        "2t",
        "--domain",
        "Europe",
        "--units",
        "celsius",
        "-o",
        tmp_path / "ens.png",
    )
    assert out.returncode == 0, out.stderr
    for f in ("ens_mean.png", "ens_std.png"):
        assert (tmp_path / f).read_bytes()[:4] == b"\x89PNG", f
    assert "members: 5" in out.stdout and "earthkit-transforms" in out.stdout


@pytest.mark.earthkit
def test_ens_stats_percentile_and_panel(tmp_path):
    out = uv(
        "ekplot.py",
        "ens-stats",
        ENS,
        "--param",
        "2t",
        "--stats",
        "mean,std,p90",
        "--panel",
        "-o",
        tmp_path / "all.png",
    )
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "all.png").read_bytes()[:4] == b"\x89PNG"


def test_ens_stats_output_names():
    m = load_script("ecmwf-earthkit", "ekplot")
    assert m.stat_outputs("ens.png", ["mean", "std"]) == {
        "mean": "ens_mean.png",
        "std": "ens_std.png",
    }
    assert m.parse_stats("mean,std,p10,p90") == [
        ("mean", None),
        ("std", None),
        ("p10", 10),
        ("p90", 90),
    ]
    with pytest.raises(ValueError):
        m.parse_stats("median")


def test_spread_is_labelled_as_spread():
    m = load_script("ecmwf-earthkit", "ekplot")
    assert (
        m.stat_label("std", "2 metre temperature")
        == "2 metre temperature — ensemble standard deviation"
    )
    assert (
        m.stat_label("p90", "2 metre temperature")
        == "2 metre temperature — ensemble 90th percentile"
    )
