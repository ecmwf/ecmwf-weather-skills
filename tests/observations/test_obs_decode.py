# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""obs.py decoding (pyodc, pdbufr), thermal indices (thermofeel) and scores (earthkit-meteo).

Fixtures are real MARS retrievals: ODB feedback for Lisbon SYNOP 08535 and METAR LPPT
(2026-10-09, 06 UTC window) and BUFR reports (SYNOP 08535, METARs around LPPT)."""

import json

import pytest
from conftest import FIXTURES, SKILLS, load_script

pytestmark = pytest.mark.earthkit
obs = load_script("ecmwf-observations", "obs")
SCRIPT = SKILLS / "ecmwf-observations" / "scripts" / "obs.py"
LISBON = {"wmo": "08535", "icao": None, "name": "LISBOA/GEOFISICO", "lat": 38.72, "lon": -9.15}
LPPT = {"wmo": None, "icao": "LPPT", "name": "Lisbon Arpt", "lat": 38.781, "lon": -9.136}


def pick(rows, var, t):
    return next(r for r in rows if r["variable"] == var and r["time_utc"] == t)


def test_feedback_synop_rows():
    rows = obs.decode_feedback(FIXTURES / "obs-feedback-08535.odb", LISBON)
    t = pick(rows, "t2m", "2026-10-09T06:00Z")
    assert t["value"] == pytest.approx(17.6, abs=0.01) and t["units"] == "degC"
    assert t["fg_depar"] == pytest.approx(0.75, abs=0.01) and t["qc"] == "active"
    assert t["report"] == "SYNOP" and t["source"] == obs.ECMWF_LABEL
    assert pick(rows, "td", "2026-10-09T06:00Z")["value"] == pytest.approx(6.5, abs=0.01)
    assert pick(rows, "mslp", "2026-10-09T06:00Z")["value"] == pytest.approx(1021.9, abs=0.01)
    assert pick(rows, "ps", "2026-10-09T06:00Z")["value"] == pytest.approx(1012.6, abs=0.01)
    assert pick(rows, "wind_speed", "2026-10-09T06:00Z")["value"] == pytest.approx(5.4)
    assert pick(rows, "rh", "2026-10-09T06:00Z")["value"] == pytest.approx(48.1, abs=0.1)
    p = pick(rows, "precip", "2026-10-09T06:00Z")
    assert p["value"] == 0.0 and p["period_h"] == 24 and p["units"] == "mm"
    # missing values (-3.4e38) are dropped, never reported as numbers
    assert not [r for r in rows if r["variable"] == "t2m" and r["time_utc"].endswith("08:00Z")]
    assert all(abs(r["value"]) < 1e6 for r in rows)


def test_feedback_metar_rows():
    rows = obs.decode_feedback(FIXTURES / "obs-feedback-lppt.odb", LPPT)
    t = pick(rows, "t2m", "2026-10-09T03:30Z")
    assert t["value"] == pytest.approx(17.95, abs=0.01) and t["report"] == "METAR"
    assert pick(rows, "ps", "2026-10-09T03:30Z")["value"] == pytest.approx(1007.9, abs=0.01)
    assert not [r for r in rows if r["variable"] == "mslp"]


def test_bufr_synop_rows_with_precipitation_periods():
    rows = obs.decode_bufr(FIXTURES / "obs-synop-08535.bufr", LISBON)
    # six reports; the 06 UTC one carries only extremes and gusts, which are not decoded
    assert len({r["time_utc"] for r in rows}) == 5
    # 05, 06 and 11 UTC report temperature as missing (-1e100): masked, not converted
    times = sorted(r["time_utc"] for r in rows if r["variable"] == "t2m")
    assert times == ["2026-10-06T12:00Z", "2026-10-06T17:00Z", "2026-10-06T18:00Z"]
    t = pick(rows, "t2m", "2026-10-06T12:00Z")
    assert 5 < t["value"] < 40 and t["fg_depar"] is None and t["qc"] == ""
    assert t["source"] == obs.ECMWF_LABEL and t["report"] == "SYNOP"
    periods = {r["period_h"] for r in rows if r["variable"] == "precip"}
    assert periods <= {1, 3, 6, 12, 24} and periods
    assert pick(rows, "mslp", "2026-10-06T12:00Z")["units"] == "hPa"


def test_bufr_metar_rows_filtered_by_icao():
    rows = obs.decode_bufr(FIXTURES / "obs-metar-lppt.bufr", LPPT)
    assert {r["station"] for r in rows} == {"LPPT"}
    t = pick(rows, "t2m", "2026-10-07T14:30Z")
    assert t["value"] == pytest.approx(23.95, abs=0.01) and t["report"] == "METAR"
    assert pick(rows, "qnh", "2026-10-07T14:30Z")["value"] == pytest.approx(1017.5)


def test_thermal_indices_from_temperature_dewpoint_and_wind():
    rows = obs.decode_feedback(FIXTURES / "obs-feedback-08535.odb", LISBON)
    out, notes = obs.compute_indices(rows)
    names = {r["variable"] for r in out}
    assert {"heat_index", "humidex", "apparent_temperature", "wind_chill"} <= names
    h = pick(out, "humidex", "2026-10-09T06:00Z")
    assert 10 < h["value"] < 25 and h["units"] == "degC" and "thermofeel" in h["source"]
    # 08:00 and 09:00 have no 2 m temperature: reported, not invented
    assert any("08:00" in n for n in notes)


def test_scores_with_earthkit_meteo():
    pairs = [
        {"step": 0, "variable": "t2m", "forecast": 18.0, "observed": 17.0},
        {"step": 3, "variable": "t2m", "forecast": 17.0, "observed": 18.0},
        {"step": 6, "variable": "t2m", "forecast": 16.0, "observed": 13.0},
    ]
    s = obs.scores(pairs)["t2m"]
    assert s["overall"]["n"] == 3
    assert s["overall"]["bias"] == pytest.approx(1.0)
    assert s["overall"]["mae"] == pytest.approx(5 / 3, abs=0.01)
    assert s["overall"]["rmse"] == pytest.approx((11 / 3) ** 0.5, abs=0.01)
    assert s["by_step"]["6"]["bias"] == pytest.approx(3.0)


def test_cli_series_decodes_a_local_file_to_csv_and_json(tmp_path):
    csv_out = tmp_path / "t2m.csv"
    rc = obs.main(
        [
            "series",
            "--station",
            "08535",
            "--file",
            str(FIXTURES / "obs-feedback-08535.odb"),
            "--vars",
            "t2m,td",
            "--tz",
            "Europe/Lisbon",
            "--csv",
            "-o",
            str(csv_out),
        ]
    )
    assert rc == 0
    lines = csv_out.read_text().splitlines()
    assert lines[0].startswith("station,time_utc,local_time,variable,value,units")
    assert any(",t2m,17.6," in ln and "2026-10-09T07:00+01:00" in ln for ln in lines)
    assert not any(",mslp," in ln for ln in lines)


def test_cli_series_json_stdout_is_clean(capsys):
    rc = obs.main(
        [
            "series",
            "--station",
            "08535",
            "--file",
            str(FIXTURES / "obs-feedback-08535.odb"),
            "--indices",
            "--json",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["source"] == obs.ECMWF_LABEL and out["rows"]
    assert out["summary"]["t2m"]["max"]["value"] == pytest.approx(18.3, abs=0.01)
    assert "licence" in out["attribution"].lower() or "ecmwf" in out["attribution"].lower()


def test_cli_verify_pairs_scores_and_merged_json(tmp_path, capsys):
    fc = {
        "run": "2026-10-09T00:00Z",
        "model": "ifs",
        "gridpoint": {"lat": 38.75, "lon": -9.25},
        "units": {"t2m_C": "degC"},
        "series": [
            {"step": s, "valid_time": f"2026-10-09T{s:02d}:00Z", "t2m_C": 17.0} for s in (4, 5, 6)
        ],
    }
    (tmp_path / "fc.json").write_text(json.dumps(fc))
    rc = obs.main(
        [
            "verify",
            "--station",
            "08535",
            "--forecast",
            str(tmp_path / "fc.json"),
            "--file",
            str(FIXTURES / "obs-feedback-08535.odb"),
            "-o",
            str(tmp_path / "pairs.csv"),
            "--meteogram-json",
            str(tmp_path / "merged.json"),
            "--json",
        ]
    )
    assert rc == 0
    r = json.loads(capsys.readouterr().out)
    s = r["scores"]["t2m"]["overall"]
    assert s["n"] == 3 and s["bias"] < 0  # forecast 17.0 vs observed 18.3 / 17.8 / 17.6
    assert 5 < r["gridpoint_distance_km"] < 15
    assert (tmp_path / "pairs.csv").read_text().count("\n") == 4
    merged = json.loads((tmp_path / "merged.json").read_text())
    assert merged["observations"]["series"] and merged["series"] == fc["series"]
