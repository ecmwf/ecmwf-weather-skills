# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess

import pytest
from conftest import FIXTURES, SKILLS, load_script

GRIB = FIXTURES / "ifs-sfc-5deg-20261002-00z.grib2"
SCRIPT = SKILLS / "ecmwf-open-data" / "scripts" / "odpoint.py"


@pytest.fixture(scope="module")
def odp():
    return load_script("ecmwf-open-data", "odpoint")


# --- pure helpers (no earthkit needed: module must import without it) ------------------------


def test_module_imports_without_earthkit(odp):
    assert hasattr(odp, "point_series")


@pytest.mark.earthkit
def test_deaccumulate_m_to_mm_intervals(odp):
    assert odp.deaccumulate([0.0, 0.001, 0.0035, 0.0035]) == [0.0, 1.0, 2.5, 0.0]


@pytest.mark.earthkit
def test_deaccumulate_clips_negative_noise(odp):
    assert odp.deaccumulate([0.0, 0.002, 0.0019999]) == [0.0, 2.0, 0.0]


@pytest.mark.earthkit
def test_deaccumulate_first_value_unknown_when_series_does_not_start_at_step_0(odp):
    # steps 12, 24: the value at 12 is accumulated over 0-12, not "since previous step"
    assert odp.deaccumulate([0.004, 0.005], steps=[12, 24]) == [None, 1.0]
    assert odp.deaccumulate([0.0, 0.001], steps=[0, 6]) == [0.0, 1.0]


def test_access_note_without_credentials_mentions_size_and_polytope(odp, tmp_path):
    note = odp.access_note(198_000_000, env={}, home=tmp_path)
    assert "Polytope" in note and "MB" in note


def test_access_note_with_polytope_key(odp, tmp_path):
    note = odp.access_note(198_000_000, env={"POLYTOPE_USER_KEY": "x"}, home=tmp_path)
    assert "ecmwf-polytope skill" in note.lower()


def test_access_note_with_ecmwfapirc(odp, tmp_path):
    (tmp_path / ".ecmwfapirc").write_text("{}")
    assert "ecmwf-polytope skill" in odp.access_note(1, env={}, home=tmp_path).lower()


def test_default_fetcher_is_parallel(odp):
    args = odp.build_parser().parse_args(["--lat", "1", "--lon", "2"])
    assert args.fetcher == "parallel" and args.workers == 8


def test_normalise_lon(odp):
    assert odp.normalise_lon(350.0) == -10.0
    assert odp.normalise_lon(-9.0) == -9.0


# --- earthkit-backed -------------------------------------------------------------------------


@pytest.mark.earthkit
def test_point_series_from_fixture(odp):
    import earthkit.data as ekd

    fl = ekd.from_source("file", str(GRIB)).to_fieldlist()
    res = odp.point_series(fl, lat=38.72, lon=-9.14)  # Lisbon; 5° grid → (40, -10)
    gp = res["gridpoint"]
    assert gp["lat"] == 40.0 and gp["lon"] == -10.0
    assert 0 < gp["distance_km"] < 300
    rows = res["series"]
    assert [r["step"] for r in rows] == [0, 6, 12]
    r = rows[2]
    assert r["valid_time"] == "2026-10-02T12:00Z"
    assert -60 < r["t2m_C"] < 60  # converted from K
    assert 0 <= r["wind_speed_ms"] < 80  # earthkit-meteo
    assert 0 <= r["wind_dir_deg"] <= 360
    assert 900 < r["msl_hPa"] < 1100
    assert 0 <= r["tcc_pct"] <= 100
    assert r["precip_mm"] >= 0
    assert res["units"]["t2m_C"] == "degC"


@pytest.mark.earthkit
def test_cli_from_file_json():
    out = subprocess.run(
        [
            "uv",
            "run",
            "--quiet",
            str(SCRIPT),
            "--lat",
            "38.72",
            "--lon",
            "-9.14",
            "--file",
            str(GRIB),
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["series"] and data["attribution"]["licence"] == "CC-BY-4.0"
    assert "note" in data


@pytest.mark.live
@pytest.mark.earthkit
def test_live_point_forecast_small():
    out = subprocess.run(
        [
            "uv",
            "run",
            "--quiet",
            str(SCRIPT),
            "--lat",
            "51.5",
            "--lon",
            "-0.12",
            "--steps",
            "0-12",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert len(data["series"]) == 5 and data["run"]


@pytest.mark.earthkit
def test_shape_output_local_time_from_now_daily(odp):
    from datetime import datetime, timezone

    rows = [
        {
            "step": s,
            "valid_time": f"2026-10-03T{s:02d}:00Z",
            "t2m_C": 10.0 + s,
            "precip_mm": 1.0 if s else 0.0,
        }
        for s in range(0, 24, 3)
    ]
    rows += [{"step": 24, "valid_time": "2026-10-04T00:00Z", "t2m_C": 5.0, "precip_mm": 2.0}]
    out = odp.shape_output(
        rows,
        tz="Europe/London",
        from_now=True,
        daily=True,
        now=datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc),
    )
    assert out["series"][0]["valid_time"] == "2026-10-03T06:00Z"
    assert out["series"][0]["local_time"] == "2026-10-03T07:00+01:00"
    d = {x["date"]: x for x in out["daily"]}
    # local day 3 Oct = 01:00..23:00 BST = 00..22 UTC rows (00 UTC is 01 BST)
    assert d["2026-10-03"]["high_C"] == 31.0 and d["2026-10-03"]["low_C"] == 10.0
    assert d["2026-10-03"]["precip_mm"] == 7.0 and d["2026-10-03"]["partial"] is False
    assert d["2026-10-04"]["high_C"] == 5.0 and d["2026-10-04"]["partial"] is True
    assert "local_time" not in rows[0]  # inputs untouched


def test_build_parser_has_tz_from_now_daily(odp):
    a = odp.build_parser().parse_args(
        ["--lat", "1", "--lon", "2", "--tz", "UTC", "--from-now", "--daily"]
    )
    assert a.tz == "UTC" and a.from_now and a.daily


def test_next_hours_window_open_data(odp):
    from datetime import datetime, timezone

    rows = [{"step": s, "valid_time": f"2026-10-03T{s:02d}:00Z"} for s in range(0, 24, 3)]
    out = odp.shape_output(rows, next_hours=6, now=datetime(2026, 10, 3, 5, 0, tzinfo=timezone.utc))
    assert [r["valid_time"][11:13] for r in out["series"]] == ["06", "09"]
    assert (
        odp.build_parser().parse_args(["--lat", "1", "--lon", "2", "--next-hours", "24"]).next_hours
        == 24
    )


def test_access_note_offers_instructions(odp, tmp_path):
    assert "instructions" in odp.access_note(1_000_000, env={}, home=tmp_path)


def test_earthkit_barrier(odp):
    b = odp.earthkit_barrier("No module named 'earthkit'")
    s = "\n".join(b["user_steps"])
    assert "https://docs.astral.sh/uv" in s and "WSL" in s and "odcatalog.py download" in b["agent"]


# --- feels-like indices (thermofeel) -------------------------------------------------------------

# thermofeel's own regression cases (tests/thermofeel_testcases.csv rows 1-3 and the expected
# hia.csv, humidex.csv, at.csv values in K), plus a cold, windy case for wind chill.
THERMOFEEL_CASES = {
    "t2_k": [310.0, 300.0, 277.2389906, 263.15],
    "td_k": [280.0, 290.0, 273.1714606, 258.15],
    "va": [2.0, 0.02, 0.593496491, 5.0],
}
EXPECTED_K = {
    "heat_index_C": [307.7372555883379732, 300.6820230127044624, 277.2389906],
    "humidex_C": [309.9589063878618163, 305.1904702735971000, 275.0833884332144521],
    "apparent_temperature_C": [307.8597961043828946, 302.3002187043325648, 274.8403862713729495],
}
COMFORT = FIXTURES / "ifs-od-comfort-5deg-20261009-00z.grib2"
CLI = ["uv", "run", "--quiet", str(SCRIPT), "--lat", "37.39", "--lon", "-5.98"]  # Seville


def test_indices_flag_adds_dewpoint(odp):
    assert odp.request_params("2t,tp,10u,10v", indices=True) == ["2t", "tp", "10u", "10v", "2d"]
    assert odp.request_params("2t,2d", indices=True) == ["2t", "2d", "10u", "10v"]
    assert odp.request_params("2t,tp", indices=False) == ["2t", "tp"]


@pytest.mark.earthkit
def test_feels_like_reproduces_thermofeel_reference_values(odp):
    import numpy as np

    c = THERMOFEEL_CASES
    out = odp.feels_like(np.array(c["t2_k"]), np.array(c["td_k"]), np.array(c["va"]), np.zeros(4))
    for key, kelvin in EXPECTED_K.items():
        np.testing.assert_allclose(out[key][:3], np.array(kelvin) - 273.15, atol=1e-6)
    # Wind chill only inside its validity range (-50..5 °C, 5..80 km/h): the warm and calm
    # rows have none; -10 °C at 5 m/s (18 km/h) feels like about -17.4 °C.
    assert np.isnan(out["wind_chill_C"][:3]).all()
    assert out["wind_chill_C"][3] == pytest.approx(-17.4, abs=0.1)


@pytest.mark.earthkit
def test_point_series_with_indices_from_open_data_fields(odp):
    import earthkit.data as ekd

    fl = ekd.from_source("file", str(COMFORT)).to_fieldlist()
    res = odp.point_series(fl, lat=37.39, lon=-5.98, indices=True)  # Seville
    keys = ("heat_index_C", "humidex_C", "apparent_temperature_C", "wind_chill_C")
    assert all(res["units"][k] == "degC" for k in keys)
    r = res["series"][-1]
    assert all(k in r for k in keys)
    assert abs(r["apparent_temperature_C"] - r["t2m_C"]) < 15
    assert res["indices_note"].startswith("Feels-like indices from thermofeel")


@pytest.mark.earthkit
def test_point_series_indices_need_dewpoint(odp):
    import earthkit.data as ekd

    fl = ekd.from_source("file", str(GRIB)).to_fieldlist()
    with pytest.raises(ValueError, match="2d"):
        odp.point_series(fl, lat=38.72, lon=-9.14, indices=True)


@pytest.mark.earthkit
def test_cli_indices_csv_columns():
    out = subprocess.run(
        [*CLI, "--file", str(COMFORT), "--indices", "--csv"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.returncode == 0, out.stderr
    header = out.stdout.splitlines()[0].split(",")
    assert {"t2m_C", "heat_index_C", "humidex_C", "apparent_temperature_C"} <= set(header)


@pytest.mark.earthkit
def test_cli_table_shows_empty_cells_for_missing_values():
    # Wind chill is empty in warm weather, precipitation at the first step when the series
    # starts after step 0: the table prints blanks, not a crash.
    out = subprocess.run(
        [*CLI, "--file", str(COMFORT), "--indices"],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.returncode == 0, out.stderr
    lines = out.stdout.splitlines()
    assert any(line.startswith("2026-10-09T12:00Z") for line in lines)
    assert any(line.startswith("Indices: Feels-like indices") for line in lines)
