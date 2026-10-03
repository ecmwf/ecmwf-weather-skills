# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess

import pytest
from conftest import FIXTURES, SKILLS, load_script

GRIB = FIXTURES / "ifs-sfc-5deg-20261002-00z.grib2"
SCRIPT = SKILLS / "open-data" / "scripts" / "odpoint.py"


@pytest.fixture(scope="module")
def odp():
    return load_script("open-data", "odpoint")


# --- pure helpers (no earthkit needed: module must import without it) ------------------------


def test_module_imports_without_earthkit(odp):
    assert hasattr(odp, "point_series")


def test_deaccumulate_m_to_mm_intervals(odp):
    assert odp.deaccumulate([0.0, 0.001, 0.0035, 0.0035]) == [0.0, 1.0, 2.5, 0.0]


def test_deaccumulate_clips_negative_noise(odp):
    assert odp.deaccumulate([0.0, 0.002, 0.0019999]) == [0.0, 2.0, 0.0]


def test_deaccumulate_first_value_unknown_when_series_does_not_start_at_step_0(odp):
    # steps 12, 24: the value at 12 is accumulated over 0-12, not "since previous step"
    assert odp.deaccumulate([0.004, 0.005], steps=[12, 24]) == [None, 1.0]
    assert odp.deaccumulate([0.0, 0.001], steps=[0, 6]) == [0.0, 1.0]


def test_access_note_without_credentials_mentions_size_and_polytope(odp, tmp_path):
    note = odp.access_note(198_000_000, env={}, home=tmp_path)
    assert "Polytope" in note and "MB" in note


def test_access_note_with_polytope_key(odp, tmp_path):
    note = odp.access_note(198_000_000, env={"POLYTOPE_USER_KEY": "x"}, home=tmp_path)
    assert "polytope skill" in note.lower()


def test_access_note_with_ecmwfapirc(odp, tmp_path):
    (tmp_path / ".ecmwfapirc").write_text("{}")
    assert "polytope skill" in odp.access_note(1, env={}, home=tmp_path).lower()


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
