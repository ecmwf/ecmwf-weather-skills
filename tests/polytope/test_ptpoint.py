# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from datetime import datetime, timezone

import pytest
from conftest import FIXTURES, SKILLS, load_script

pt = load_script("polytope", "ptpoint")
OPER = json.loads((FIXTURES / "polytope-oper-lisbon.covjson").read_text())
ENFO = json.loads((FIXTURES / "polytope-enfo-lisbon.covjson").read_text())
SCRIPT = SKILLS / "polytope" / "scripts" / "ptpoint.py"
UTC = timezone.utc


# --- credentials (never print secrets) --------------------------------------------------------


def test_credentials_none(tmp_path):
    assert pt.credentials(env={}, home=tmp_path) is None


def test_credentials_env_and_files(tmp_path):
    assert pt.credentials(env={"POLYTOPE_USER_KEY": "k"}, home=tmp_path) == "POLYTOPE_USER_KEY"
    (tmp_path / ".ecmwfapirc").write_text("{}")
    assert pt.credentials(env={}, home=tmp_path) == "~/.ecmwfapirc"
    (tmp_path / ".polytopeapirc").write_text("{}")
    assert pt.credentials(env={}, home=tmp_path) == "~/.polytopeapirc"


# --- request ---------------------------------------------------------------------------------


def test_build_request_deterministic():
    r = pt.build_request(38.72, -9.14, date="20261001", time="0000", end_step=48)
    assert r["stream"] == "oper" and r["type"] == "fc" and "number" not in r
    assert r["param"] == "167/228/165/166/151/164"
    f = r["feature"]
    assert f["type"] == "timeseries" and f["points"] == [[38.72, -9.14]]
    assert f["axes"] == ["latitude", "longitude"] and f["time_axis"] == "step"
    assert f["range"] == {"start": 0, "end": 48}
    assert "format" not in r  # the server rejects format=covjson


def test_build_request_ensemble():
    r = pt.build_request(51.45, -0.97, date="20261001", time="1200", end_step=120, ensemble=True)
    assert r["stream"] == "enfo" and r["type"] == "pf" and r["number"] == "1/to/50"
    assert r["param"] == "167/228"


def test_candidate_runs_latest_first():
    runs = pt.candidate_runs(datetime(2026, 10, 2, 20, 0, tzinfo=UTC))
    assert runs[:3] == [
        ("20261002", "1200"),
        ("20261002", "0000"),
        ("20261001", "1200"),
    ]
    early = pt.candidate_runs(datetime(2026, 10, 2, 3, 0, tzinfo=UTC))
    assert early[0] == ("20261001", "1200")


# --- parsing -------------------------------------------------------------------------------------


def test_parse_covjson_deterministic():
    p = pt.parse_covjson(OPER)
    assert p["run"].endswith("T00:00Z")
    assert abs(p["gridpoint"]["lat"] - 38.7) < 0.1 and -9.5 < p["gridpoint"]["lon"] < -9.0
    assert list(p["members"]) == [0]
    m = p["members"][0]
    assert set(m) == {"2t", "tp", "10u", "10v", "msl", "tcc"}
    assert len(p["times"]) == len(m["2t"]) == 13


def test_parse_covjson_ensemble_members():
    p = pt.parse_covjson(ENFO)
    assert sorted(p["members"]) == [1, 2, 3, 4, 5]


def test_quantiles_pure():
    assert pt.quantiles([[1, 10], [2, 20], [3, 30], [4, 40], [5, 50]], (10, 50, 90)) == [
        [1.4, 3.0, 4.6],
        [14.0, 30.0, 46.0],
    ]


def test_size_note_mentions_kb():
    assert "KB" in pt.size_note(9170)


def test_licence_for_operational_data():
    a = pt.attribution(OPER)
    assert "©" in a["short"] and "licence" in a["note"].lower()


# --- earthkit-backed derived output --------------------------------------------------------


@pytest.mark.earthkit
def test_series_deterministic_units():
    rows = pt.series(pt.parse_covjson(OPER))
    r = rows[6]
    assert -40 < r["t2m_C"] < 50 and 900 < r["msl_hPa"] < 1100
    assert 0 <= r["wind_dir_deg"] <= 360 and 0 <= r["tcc_pct"] <= 100
    assert rows[0]["precip_mm"] == 0.0 and r["precip_mm"] >= 0


@pytest.mark.earthkit
def test_series_ensemble_quantiles():
    rows = pt.series(pt.parse_covjson(ENFO))
    r = rows[6]
    assert r["t2m_C_p10"] <= r["t2m_C_p50"] <= r["t2m_C_p90"]
    assert r["members"] == 5


# --- CLI -----------------------------------------------------------------------------------------


def test_cli_check_without_credentials(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--json"],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 4
    d = json.loads(out.stdout)
    assert d["polytope"] is False and "open-data" in d["fallback"]


@pytest.mark.live
@pytest.mark.earthkit
def test_live_point():
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
            "--steps",
            "0-24",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert len(d["series"]) >= 13 and d["source"] == "polytope"
