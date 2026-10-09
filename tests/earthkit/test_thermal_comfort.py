# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0
"""Thermal-comfort indices (thermofeel) in ekplot.py: `indices` maps and the meteogram panel.

Fixtures (5° global, created once and committed; < 100 KB each):

- ifs-od-comfort-5deg-20261009-00z.grib2 — ECMWF Open Data IFS 00 UTC run of 2026-10-09,
  2t 2d 10u 10v sp ssrd ssr strd str at steps 6 and 12 (no fdir: Open Data lacks it):
      fl = ekd.from_source("ecmwf-open-data", model="ifs", stream="oper", type="fc",
               levtype="sfc", param=[...], step=[6, 12], time=0).to_fieldlist()
      earthkit.geo.regrid(fl, out_grid={"grid": [5, 5]}, interpolation="linear")
          .to_target("file", ...)
- ifs-rad-5deg.grib2 — MARS, IFS HRES 00 UTC run of 2026-10-02, steps 6/12, grid=5/5,
  param=167/168/165/166/134/169/176/175/177/228021 (2t 2d 10u 10v sp ssrd ssr strd str fdir),
  retrieved with `uv run mars.py retrieve` and re-encoded as GRIB2 with grid_ccsds packing
  (`f.set({"metadata.edition": 2, "metadata.packingType": "grid_ccsds"}).sync()`, lossless).
"""

import json
import subprocess
from datetime import datetime

import pytest
from conftest import FIXTURES, SKILLS, load_script

OD = FIXTURES / "ifs-od-comfort-5deg-20261009-00z.grib2"
RAD = FIXTURES / "ifs-rad-5deg.grib2"
SCRIPTS = SKILLS / "ecmwf-earthkit" / "scripts"


def uv(script, *args, timeout=600):
    return subprocess.run(
        ["uv", "run", "--quiet", str(SCRIPTS / script), *map(str, args)],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.fixture(scope="module")
def ekp():
    return load_script("ecmwf-earthkit", "ekplot")


# --- pure (no earthkit) -------------------------------------------------------------------------


def test_index_table_covers_the_cli_choices(ekp):
    assert list(ekp.INDICES) == [
        "heat-index",
        "humidex",
        "apparent-temperature",
        "wind-chill",
        "utci",
        "wbgt",
        "mrt",
    ]
    assert {k for k, v in ekp.INDICES.items() if v["radiation"]} == {"utci", "wbgt", "mrt"}
    for spec in ekp.INDICES.values():
        assert spec["levels"] == sorted(spec["levels"]) and spec["long_name"]


def test_utci_levels_are_the_stress_categories(ekp):
    # UTCI assessment scale (Bröde et al. 2012): category boundaries in °C.
    assert ekp.INDICES["utci"]["levels"] == [-40, -27, -13, 0, 9, 26, 32, 38, 46]


FEELS = {
    "units": {"t2m_C": "degC", "heat_index_C": "degC", "apparent_temperature_C": "degC"},
    "series": [
        {
            "step": 0,
            "valid_time": "2026-07-02T12:00Z",
            "t2m_C": 36.9,
            "heat_index_C": 34.6,
            "apparent_temperature_C": 34.7,
        },
        {
            "step": 6,
            "valid_time": "2026-07-02T18:00Z",
            "t2m_C": 26.9,
            "heat_index_C": None,
            "apparent_temperature_C": 29.2,
        },
    ],
}


def test_meteogram_feels_like_panel_follows_temperature(ekp):
    panels = ekp.meteogram_panels(FEELS)
    assert [p["key"] for p in panels] == ["t2m_C", "feels_like"]
    lines = {line["key"]: line for line in panels[1]["lines"]}
    assert list(lines) == ["t2m_C", "heat_index_C", "apparent_temperature_C"]
    assert lines["apparent_temperature_C"]["values"] == [34.7, 29.2]
    assert panels[1]["units"] == "degC"


def test_meteogram_feels_like_skips_an_index_without_values(ekp):
    pj = json.loads(json.dumps(FEELS))
    pj["units"]["wind_chill_C"] = "degC"
    for r in pj["series"]:
        r["wind_chill_C"] = None
    lines = ekp.meteogram_panels(pj)[1]["lines"]
    assert "wind_chill_C" not in [line["key"] for line in lines]


def test_meteogram_feels_like_says_ensemble_values_are_medians(ekp):
    pj = json.loads(json.dumps(FEELS))
    for r in pj["series"]:
        r["t2m_C_p50"] = r["t2m_C"]
    assert ekp.meteogram_panels(pj)[1]["title"].endswith("ensemble medians")
    assert "median" not in ekp.meteogram_panels(FEELS)[1]["title"]


def test_meteogram_without_indices_has_no_feels_like_panel(ekp):
    pj = {"units": {"t2m_C": "degC"}, "series": FEELS["series"]}
    assert [p["key"] for p in ekp.meteogram_panels(pj)] == ["t2m_C"]


# --- earthkit + thermofeel ----------------------------------------------------------------------


@pytest.mark.earthkit
def test_heat_index_matches_thermofeel_on_the_fields(ekp):
    import earthkit.data as ekd
    import numpy as np
    import thermofeel as tf

    r = ekp.compute_index(str(OD), "heat-index", step=12)
    fl = ekd.from_source("file", str(OD)).to_fieldlist()
    t2 = fl.sel({"parameter.variable": "2t", "time.step": 12})[0].to_numpy()
    td = fl.sel({"parameter.variable": "2d", "time.step": 12})[0].to_numpy()
    np.testing.assert_allclose(r["values"], tf.calculate_heat_index_adjusted(t2, td))
    assert r["values"].shape == r["lat"].shape == (37, 72)
    assert r["step"] == 12 and r["units"] == "K"
    assert r["valid_time"] == datetime(2026, 10, 9, 12)


@pytest.mark.earthkit
def test_default_step_is_the_last_one(ekp):
    assert ekp.compute_index(str(OD), "humidex")["step"] == 12


@pytest.mark.earthkit
def test_radiation_flux_is_the_mean_over_the_interval_between_consecutive_steps(ekp):
    import earthkit.data as ekd
    import numpy as np

    fluxes, (begin, end) = ekp.radiation_fluxes(str(RAD), step=12)
    assert (begin, end) == (datetime(2026, 10, 2, 6), datetime(2026, 10, 2, 12))
    fl = ekd.from_source("file", str(RAD)).to_fieldlist()
    acc = fl.sel({"parameter.variable": "ssrd"}).to_numpy()  # steps 6, 12 (J m-2)
    np.testing.assert_allclose(fluxes["ssrd"], (acc[1] - acc[0]) / (6 * 3600))
    # not the mean since the run started (acc / (12 * 3600)): they differ by hundreds of W m-2
    assert np.abs(fluxes["ssrd"] - acc[1] / (12 * 3600)).max() > 100
    assert set(fluxes) == {"ssrd", "ssr", "strd", "str", "fdir"}


@pytest.mark.earthkit
def test_first_step_interval_starts_at_the_run(ekp):
    _, (begin, end) = ekp.radiation_fluxes(str(RAD), step=6)
    assert (begin, end) == (datetime(2026, 10, 2, 0), datetime(2026, 10, 2, 6))


@pytest.mark.earthkit
def test_utci_from_mars_fields_matches_thermofeel(ekp):
    import numpy as np
    import thermofeel as tf
    from earthkit.meteo import solar

    r = ekp.compute_index(str(RAD), "utci", step=12)
    fx, (begin, end) = ekp.radiation_fluxes(str(RAD), step=12)
    f = ekp.comfort_fields(str(RAD), 12)
    cossza = solar.cos_solar_zenith_angle_integrated(begin, end, f["lat"], f["lon"])
    dsrp = tf.approximate_dsrp(fx["fdir"], cossza)
    mrt = tf.calculate_mean_radiant_temperature(
        fx["ssrd"], fx["ssr"], dsrp, fx["strd"], fx["fdir"], fx["str"], cossza
    )
    va = np.hypot(f["fields"]["10u"], f["fields"]["10v"])
    utci = tf.calculate_utci(t2_k=f["fields"]["2t"], va=va, mrt=mrt, td_k=f["fields"]["2d"])
    np.testing.assert_allclose(r["values"], utci)
    assert "estimated" not in r["note"]
    # Early-October daytime Europe: between strong cold stress (windy Arctic coasts) and very
    # strong heat stress; Antarctica, outside the UTCI fit range, extrapolates far below -40 °C,
    # so only a European box is checked.
    europe = (r["lat"] >= 35) & (r["lat"] <= 70) & ((r["lon"] <= 30) | (r["lon"] >= 350))
    assert r["values"][europe].min() > 253 and r["values"][europe].max() < 311  # -20..38 °C


@pytest.mark.earthkit
def test_radiation_index_without_fdir_is_blocked(ekp):
    with pytest.raises(ekp.FdirMissing):
        ekp.compute_index(str(OD), "utci", step=12)


@pytest.mark.earthkit
def test_radiation_index_at_step_0_is_refused(ekp):
    # Accumulations are zero at step 0: there is no interval to average over.
    with pytest.raises(ValueError, match="step 0"):
        ekp.compute_index(str(RAD), "mrt", step=0)


@pytest.mark.earthkit
@pytest.mark.parametrize("method", ["erbs", "disc"])
def test_approximate_fdir_is_labelled_a_demonstration(ekp, method):
    import numpy as np

    r = ekp.compute_index(str(OD), "wbgt", step=12, approximate_fdir=method)
    assert f"fdir estimated ({method.upper() if method == 'disc' else 'Erbs'})" in r["note"]
    assert "demonstration" in r["note"]
    assert np.isfinite(r["values"]).any()


@pytest.mark.earthkit
def test_non_radiation_index_missing_param_lists_what_is_there(ekp):
    with pytest.raises(ValueError, match="2d"):
        ekp.compute_index(str(FIXTURES / "ifs-sfc-5deg-20261002-00z.grib2"), "humidex")


@pytest.mark.earthkit
def test_cli_heat_index_png(tmp_path):
    png = tmp_path / "hi.png"
    out = uv("ekplot.py", "indices", OD, "--index", "heat-index", "--domain", "Europe", "-o", png)
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG"
    assert "saved" in out.stdout and "thermofeel" in out.stdout


@pytest.mark.earthkit
def test_cli_utci_png_from_mars_fields(tmp_path):
    png = tmp_path / "utci.png"
    out = uv("ekplot.py", "indices", RAD, "--index", "utci", "--step", "12", "-o", png)
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG"


@pytest.mark.earthkit
def test_cli_without_fdir_prints_a_blocked_report(tmp_path):
    out = uv("ekplot.py", "indices", OD, "--index", "utci", "-o", tmp_path / "u.png")
    assert out.returncode == 4
    assert "BLOCKED" in out.stderr and "fdir" in out.stderr
    assert "--approximate-fdir" in out.stderr and "228021" in out.stderr
    assert not (tmp_path / "u.png").exists()


@pytest.mark.earthkit
def test_cli_approximate_fdir_png(tmp_path):
    png = tmp_path / "w.png"
    out = uv("ekplot.py", "indices", OD, "--index", "wbgt", "--approximate-fdir", "erbs", "-o", png)
    assert out.returncode == 0, out.stderr
    assert "demonstration" in out.stdout
    assert png.read_bytes()[:4] == b"\x89PNG"


@pytest.mark.earthkit
def test_meteogram_png_with_feels_like(tmp_path):
    j = tmp_path / "p.json"
    j.write_text(json.dumps({**FEELS, "attribution": {"short": "Data: © 2026 ECMWF"}}))
    png = tmp_path / "p.png"
    out = uv("ekplot.py", "meteogram", j, "-o", png)
    assert out.returncode == 0, out.stderr
    assert png.read_bytes()[:4] == b"\x89PNG"
