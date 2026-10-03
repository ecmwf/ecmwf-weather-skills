# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

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


def test_candidate_runs_include_06_18_when_the_range_fits():
    runs = pt.candidate_runs(datetime(2026, 10, 2, 20, 0, tzinfo=UTC), end_step=48)
    assert runs[:3] == [("20261002", "1200"), ("20261002", "0600"), ("20261002", "0000")]
    late = pt.candidate_runs(datetime(2026, 10, 3, 2, 0, tzinfo=UTC), end_step=144)
    assert late[0] == ("20261002", "1800")


def test_candidate_runs_long_range_only_00_12():
    runs = pt.candidate_runs(datetime(2026, 10, 2, 20, 0, tzinfo=UTC), end_step=240)
    assert all(t in ("0000", "1200") for _, t in runs)


# --- local time, from-now and daily summaries -----------------------------------------------


def _p(members, start="2026-10-03T00:00Z", hours=48, step=1):
    t0 = datetime.strptime(start, "%Y-%m-%dT%H:%MZ").replace(tzinfo=UTC)
    times = [
        (t0 + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%MZ") for h in range(0, hours + 1, step)
    ]
    return {
        "run": start,
        "times": times,
        "steps": list(range(0, hours + 1, step)),
        "members": members,
    }


def test_add_local_time_and_drop_past():
    rows = [
        {"valid_time": "2026-10-03T10:00Z"},
        {"valid_time": "2026-10-03T12:00Z"},
        {"valid_time": "2026-10-03T13:00Z"},
    ]
    pt.add_local_time(rows, "Europe/London")
    assert rows[0]["local_time"] == "2026-10-03T11:00+01:00"
    kept = pt.drop_past(rows, now=datetime(2026, 10, 3, 11, 30, tzinfo=UTC))
    assert [r["valid_time"] for r in kept] == ["2026-10-03T12:00Z", "2026-10-03T13:00Z"]


def test_daily_summary_deterministic_local_days():
    n = 49
    temps = [273.15 + 10 + (h % 24) for h in range(n)]  # 10..33 C each UTC day
    tp = [0.001 * h for h in range(n)]  # 1 mm per hour
    days = pt.daily_summary(_p({0: {"2t": temps, "tp": tp}}), tz="UTC")
    d0 = days[0]
    assert d0["date"] == "2026-10-03" and d0["high_C"] == 33.0 and d0["low_C"] == 10.0
    assert d0["precip_mm"] == 23.0 and d0["partial"] is False  # 01..23 h intervals of day 1
    assert days[-1]["partial"] is True  # only 00 h of the last day


def test_daily_summary_ensemble_percentiles_of_member_highs_and_lows():
    members = {
        n: {"2t": [273.15 + n + (h % 24) for h in range(49)], "tp": [0.0] * 49}
        for n in range(1, 11)
    }
    d0 = pt.daily_summary(_p(members), tz="UTC")[0]
    # member n: low n °C, high n+23 °C
    assert d0["low_C_p50"] == 5.5 and d0["high_C_p50"] == 28.5
    assert d0["low_C_p10"] == 1.9 and d0["high_C_p90"] == 32.1
    assert d0["members"] == 10


def test_daily_summary_uses_the_local_calendar_day():
    temps = [273.15 + (30 if h == 23 else 0) for h in range(49)]  # spike at 23 UTC on day 1
    days = pt.daily_summary(_p({0: {"2t": temps, "tp": [0.0] * 49}}), tz="Europe/London")
    # 23 UTC = 00 local next day
    assert {d["date"]: d["high_C"] for d in days}["2026-10-04"] == 30.0


def test_shape_output_applies_tz_from_now_and_daily():
    p = _p({0: {"2t": [283.15] * 49, "tp": [0.0] * 49}})
    rows = [{"step": s, "valid_time": t} for s, t in zip(p["steps"], p["times"], strict=True)]
    out = pt.shape_output(
        rows,
        p,
        tz="Europe/Lisbon",
        from_now=True,
        daily=True,
        now=datetime(2026, 10, 3, 12, 0, tzinfo=UTC),
    )
    assert out["series"][0]["valid_time"] == "2026-10-03T12:00Z"
    assert out["series"][0]["local_time"] == "2026-10-03T13:00+01:00"
    assert out["timezone"] == "Europe/Lisbon" and out["daily"][0]["date"] == "2026-10-03"
    plain = pt.shape_output(list(rows), p)
    assert "daily" not in plain and "local_time" not in plain["series"][0]


def test_next_hours_window():
    # A run is at most ~13 h old when published, so request enough steps and trim to the window.
    assert pt.steps_for_next_hours(24) == 42
    rows = [{"valid_time": f"2026-10-03T{h:02d}:00Z"} for h in range(24)]
    kept = pt.window(rows, now=datetime(2026, 10, 3, 5, 30, tzinfo=UTC), hours=3)
    assert [r["valid_time"] for r in kept] == [
        "2026-10-03T06:00Z",
        "2026-10-03T07:00Z",
        "2026-10-03T08:00Z",
    ]


def test_shape_output_next_hours():
    p = _p({0: {"2t": [283.15] * 49, "tp": [0.0] * 49}})
    rows = [{"step": s, "valid_time": t} for s, t in zip(p["steps"], p["times"], strict=True)]
    out = pt.shape_output(rows, p, next_hours=6, now=datetime(2026, 10, 3, 12, 0, tzinfo=UTC))
    assert [r["valid_time"][11:13] for r in out["series"]] == ["12", "13", "14", "15", "16", "17"]
