# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone

import pytest
from conftest import FIXTURES, SKILLS, load_script

pt = load_script("ecmwf-polytope", "ptpoint")
OPER = json.loads((FIXTURES / "polytope-oper-lisbon.covjson").read_text())
ENFO = json.loads((FIXTURES / "polytope-enfo-lisbon.covjson").read_text())
SCRIPT = SKILLS / "ecmwf-polytope" / "scripts" / "ptpoint.py"
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


@pytest.mark.earthkit
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


@pytest.mark.earthkit
def test_daily_summary_deterministic_local_days():
    n = 49
    temps = [273.15 + 10 + (h % 24) for h in range(n)]  # 10..33 C each UTC day
    tp = [0.001 * h for h in range(n)]  # 1 mm per hour
    days = pt.daily_summary(_p({0: {"2t": temps, "tp": tp}}), tz="UTC")
    d0 = days[0]
    assert d0["date"] == "2026-10-03" and d0["high_C"] == 33.0 and d0["low_C"] == 10.0
    assert d0["precip_mm"] == 23.0 and d0["partial"] is False  # 01..23 h intervals of day 1
    assert days[-1]["partial"] is True  # only 00 h of the last day


@pytest.mark.earthkit
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


@pytest.mark.earthkit
def test_daily_summary_uses_the_local_calendar_day():
    temps = [273.15 + (30 if h == 23 else 0) for h in range(49)]  # spike at 23 UTC on day 1
    days = pt.daily_summary(_p({0: {"2t": temps, "tp": [0.0] * 49}}), tz="Europe/London")
    # 23 UTC = 00 local next day
    assert {d["date"]: d["high_C"] for d in days}["2026-10-04"] == 30.0


@pytest.mark.earthkit
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


def test_setup_steps_polytope():
    s = "\n".join(pt.setup_steps())
    for must in (
        "https://api.ecmwf.int/v1/key/",
        '"user_email"',
        '"user_key"',
        "~/.polytopeapirc",
        "~/.ecmwfapirc",
        "Any authenticated ECMWF user",
        "https://www.ecmwf.int/en/forecasts/datasets",
        "research licence",
        "commercial",
        "not all datasets",
        "https://support.ecmwf.int",
    ):
        assert must in s, must


def test_cli_check_offers_setup(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--check", "--json"],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert "ptpoint.py --setup" in json.loads(out.stdout)["offer"]


def test_barriers_polytope():
    b = pt.barrier_for_error("HTTP 403 Forbidden")
    assert "https://www.ecmwf.int/en/forecasts/datasets" in "\n".join(b["user_steps"])
    assert "restricted" in b["why"]
    assert pt.barrier_for_error("no data for date") is None


def test_cli_point_without_credentials_is_blocked(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "--lat", "1", "--lon", "2"],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 4 and "BLOCKED:" in out.stderr and "odpoint.py" in out.stderr


# --- collections endpoint (authenticated) ----------------------------------------------------


def test_auth_header_sources_never_leave_the_header(tmp_path):
    (tmp_path / ".ecmwfapirc").write_text('{"url": "u", "key": "k1", "email": "a@b.int"}')
    assert pt.auth_header(env={}, home=tmp_path) == "EmailKey a@b.int:k1"
    (tmp_path / ".polytopeapirc").write_text('{"user_email": "c@d.int", "user_key": "k2"}')
    assert pt.auth_header(env={}, home=tmp_path) == "EmailKey c@d.int:k2"
    env = {"POLYTOPE_USER_KEY": "k3", "POLYTOPE_USER_EMAIL": "e@f.int"}
    assert pt.auth_header(env=env, home=tmp_path) == "EmailKey e@f.int:k3"
    assert pt.auth_header(env={"POLYTOPE_USER_KEY": "k4"}, home=tmp_path / "x") == "Bearer k4"
    assert pt.auth_header(env={}, home=tmp_path / "none") is None


def test_list_collections():
    seen = {}

    def fetch(url, headers):
        seen.update(url=url, headers=headers)
        return 200, {"message": ["cems", "ecmwf-mars"]}

    assert pt.list_collections("polytope.ecmwf.int", "Bearer k", fetch=fetch) == [
        "cems",
        "ecmwf-mars",
    ]
    assert seen["url"] == "https://polytope.ecmwf.int/api/v1/collections"
    assert seen["headers"]["Authorization"] == "Bearer k"


def test_list_collections_rejected_is_a_barrier():
    with pytest.raises(pt.AccessDenied, match="401"):
        pt.list_collections("h", "Bearer bad", fetch=lambda u, h: (401, {"message": "no"}))


def test_collection_missing_barrier():
    b = pt.collection_barrier("ecmwf-mars", ["cems"])
    assert "ecmwf-mars" in b["blocked"] and "cems" in b["why"]
    assert "https://www.ecmwf.int/en/forecasts/datasets" in "\n".join(b["user_steps"])


def test_check_reports_collections(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(pt, "credentials", lambda: "~/.ecmwfapirc")
    monkeypatch.setattr(pt, "auth_header", lambda: "Bearer zz-fake-secret-zz")
    monkeypatch.setattr(pt, "list_collections", lambda addr, auth: ["cems", "ecmwf-mars"])
    assert pt.check(True, offline=True) == 0
    raw = capsys.readouterr().out
    out = json.loads(raw)
    assert out["collections"] == ["cems", "ecmwf-mars"] and out["verified"] is True
    assert out["point_forecasts"] is True and out["verified_by"] == "collection_list"
    assert "zz-fake-secret-zz" not in raw


def test_check_without_ecmwf_mars(monkeypatch, capsys):
    monkeypatch.setattr(pt, "credentials", lambda: "~/.ecmwfapirc")
    monkeypatch.setattr(pt, "auth_header", lambda: "Bearer k")
    monkeypatch.setattr(pt, "list_collections", lambda addr, auth: ["cems"])
    assert pt.check(True) == 4
    out = json.loads(capsys.readouterr().out)
    assert out["point_forecasts"] is False and "ecmwf-mars" in out["offer"]


@pytest.mark.live
def test_live_collections():
    cols = pt.list_collections(pt.ADDRESS, pt.auth_header())
    assert "ecmwf-mars" in cols


def test_point_request_blocked_when_collection_not_available(monkeypatch, capsys):
    monkeypatch.setattr(pt, "credentials", lambda: "~/.ecmwfapirc")
    monkeypatch.setattr(pt, "auth_header", lambda: "Bearer zz")
    monkeypatch.setattr(pt, "list_collections", lambda addr, auth: ["cems"])

    def never(*a, **k):
        raise AssertionError("must not request data")

    monkeypatch.setattr(pt, "fetch_latest", never)
    assert pt.main(["--lat", "1", "--lon", "2"]) == 4
    assert "BLOCKED" in capsys.readouterr().err


# --- credentials: polytope-client's sources and malformed files ------------------------------

FAKE_KEY, FAKE_EMAIL = "zz-fake-key-zz", "fake@example.invalid"


def test_auth_header_reads_the_polytope_client_yaml_config(tmp_path):
    cfg = tmp_path / ".polytope-client"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(f"user_key: '{FAKE_KEY}'\nuser_email: {FAKE_EMAIL}\n")
    (tmp_path / ".polytopeapirc").write_text('{"user_key": "other"}')
    assert pt.auth_header(env={}, home=tmp_path) == f"EmailKey {FAKE_EMAIL}:{FAKE_KEY}"
    assert pt.credentials(env={}, home=tmp_path) == "~/.polytope-client/config.yaml"


def test_auth_header_honours_polytope_config_path(tmp_path):
    cfg = tmp_path / "elsewhere"
    cfg.mkdir()
    (cfg / "config.yaml").write_text(f'user_key: "{FAKE_KEY}"\n')
    env = {"POLYTOPE_CONFIG_PATH": str(cfg)}
    assert pt.auth_header(env=env, home=tmp_path) == f"Bearer {FAKE_KEY}"


def test_auth_header_honours_polytope_key_path(tmp_path):
    key = tmp_path / "keys.json"
    key.write_text(json.dumps({"user_key": FAKE_KEY, "user_email": FAKE_EMAIL}))
    (tmp_path / ".ecmwfapirc").write_text('{"key": "other", "email": "o@x.int"}')
    env = {"POLYTOPE_KEY_PATH": str(key)}
    assert pt.auth_header(env=env, home=tmp_path) == f"EmailKey {FAKE_EMAIL}:{FAKE_KEY}"
    assert pt.credentials(env=env, home=tmp_path) == "POLYTOPE_KEY_PATH"


def test_environment_beats_every_file(tmp_path):
    (tmp_path / ".polytopeapirc").write_text("not json")
    env = {"POLYTOPE_USER_KEY": FAKE_KEY, "POLYTOPE_USER_EMAIL": FAKE_EMAIL}
    assert pt.auth_header(env=env, home=tmp_path) == f"EmailKey {FAKE_EMAIL}:{FAKE_KEY}"


MALFORMED_POLYTOPEAPIRC = {
    "ecmwfapirc-layout": json.dumps({"key": FAKE_KEY, "email": FAKE_EMAIL}),
    "invalid-json": '{"user_key": "' + FAKE_KEY + '",',
    "empty": "",
    "yaml": f"user_key: {FAKE_KEY}\nuser_email: {FAKE_EMAIL}\n",
    "json-list": "[1, 2]",
}


@pytest.mark.parametrize("name", sorted(MALFORMED_POLYTOPEAPIRC))
def test_malformed_polytopeapirc_is_reported_not_raised(tmp_path, name):
    (tmp_path / ".polytopeapirc").write_text(MALFORMED_POLYTOPEAPIRC[name])
    with pytest.raises(pt.MalformedCredentials) as e:
        pt.auth_header(env={}, home=tmp_path)
    b = pt.malformed_barrier(e.value)
    text = json.dumps(b)
    assert b["blocked"] == "~/.polytopeapirc is malformed"
    assert FAKE_KEY not in text and FAKE_EMAIL not in text
    assert '"user_key": "<key>"' in "\n".join(b["user_steps"])


def test_malformed_names_the_keys_found(tmp_path):
    (tmp_path / ".polytopeapirc").write_text(MALFORMED_POLYTOPEAPIRC["ecmwfapirc-layout"])
    with pytest.raises(pt.MalformedCredentials) as e:
        pt.auth_header(env={}, home=tmp_path)
    assert e.value.keys == ["email", "key"] and "user_key" in e.value.problem


def test_not_utf8_polytopeapirc(tmp_path):
    (tmp_path / ".polytopeapirc").write_bytes(b"\xff\xfe\x00\x81")
    with pytest.raises(pt.MalformedCredentials, match="UTF-8"):
        pt.auth_header(env={}, home=tmp_path)


def test_ecmwfapirc_fallback_in_polytope_layout_is_malformed(tmp_path):
    (tmp_path / ".ecmwfapirc").write_text(json.dumps({"user_key": FAKE_KEY}))
    with pytest.raises(pt.MalformedCredentials) as e:
        pt.auth_header(env={}, home=tmp_path)
    assert e.value.source == "~/.ecmwfapirc" and e.value.keys == ["user_key"]


def test_check_with_malformed_file_is_blocked(monkeypatch, capsys, tmp_path):
    (tmp_path / ".polytopeapirc").write_text("[1, 2]")
    monkeypatch.setattr(pt.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(pt.os, "environ", {})

    def never(*a, **k):
        raise AssertionError("no request with a malformed key file")

    monkeypatch.setattr(pt, "list_collections", never)
    assert pt.check(False, offline=True) == 4
    assert "BLOCKED: ~/.polytopeapirc is malformed" in capsys.readouterr().err


# --- check: one tiny real feature extraction ------------------------------------------------

ADDR = "polytope.example.invalid"
JOB = f"https://{ADDR}/api/v1/requests/job-1"
TINY = json.dumps(OPER).encode()


class FakePolytope:
    """Scripted HTTP answers: POST (submit), GET (poll / download), DELETE (job removal)."""

    def __init__(self, submit, polls=(), download=(200, TINY)):
        self.submit, self.polls, self.download = list(submit), list(polls), download
        self.calls = []

    def __call__(self, method, url, headers, body=None):
        self.calls.append((method, url, dict(headers), body))
        if method == "POST":
            return self.submit.pop(0)
        if method == "DELETE":
            return 200, {}, b""
        if url.endswith("/download/job-1"):
            return self.download[0], {}, self.download[1]
        return self.polls.pop(0)


def accepted():
    return 202, {"location": "./job-1"}, b'{"status": "queued"}'


def ready():
    return 303, {"location": f"https://{ADDR}/api/v1/download/job-1"}, b""


def _check(monkeypatch, http, offline=False):
    monkeypatch.setattr(pt, "ADDRESS", ADDR)
    monkeypatch.setattr(pt, "credentials", lambda: "~/.ecmwfapirc")
    monkeypatch.setattr(pt, "auth_header", lambda: "Bearer zz-fake-secret-zz")
    monkeypatch.setattr(pt, "list_collections", lambda addr, auth: ["cems", "ecmwf-mars"])
    monkeypatch.setattr(
        pt.ecmwf_status,
        "network_barrier",
        lambda svc, err: {
            "blocked": f"cannot reach the ECMWF {svc} service",
            "why": err,
            "user_steps": ["1. retry"],
            "agent": "retry once",
            "status": {"state": "unknown"},
        },
    )
    return pt.check(True, offline=offline, http=http, sleep=lambda s: None)


def test_check_test_request_success(monkeypatch, capsys):
    http = FakePolytope([accepted()], polls=[accepted(), ready()])
    assert _check(monkeypatch, http) == 0
    raw = capsys.readouterr().out
    out = json.loads(raw)
    assert out["point_forecasts"] is True and out["verified_by"] == "test_request"
    assert out["seconds"] >= 0 and "zz-fake-secret-zz" not in raw
    method, url, _, body = http.calls[0]
    assert (method, url) == ("POST", f"https://{ADDR}/api/v1/requests/ecmwf-mars")
    sent = json.loads(body)
    assert sent["verb"] == "retrieve"
    req = json.loads(sent["request"])
    assert req["param"] == "167" and req["feature"]["range"] == {"start": 0, "end": 0}
    assert req["stream"] == "oper" and req["class"] == "od" and "grid" not in req
    assert http.calls[1][1] == JOB  # "./job-1" resolved against the submit URL
    assert ("DELETE", JOB) in [(m, u) for m, u, *_ in http.calls]


def test_check_download_on_another_host_gets_no_key(monkeypatch, capsys):
    other = (303, {"location": "https://cache.example.invalid/x/download/job-1"}, b"")
    http = FakePolytope([accepted()], polls=[other])
    assert _check(monkeypatch, http) == 0
    download = next(c for c in http.calls if "cache.example.invalid" in c[1])
    assert "Authorization" not in download[2]


def test_check_insufficient_permissions_is_an_entitlement_barrier(monkeypatch, capsys):
    msg = b'{"message": "Your request could not be processed: insufficient permissions. x"}'
    http = FakePolytope([(400, {}, msg)])
    assert _check(monkeypatch, http) == 4
    cap = capsys.readouterr()
    out = json.loads(cap.out)
    assert out["point_forecasts"] is False and "entitlement" in out["blocked"]["blocked"]
    assert "Computing Representative" in cap.err and "ecmwf-open-data" in cap.err
    assert [m for m, *_ in http.calls] == ["POST"]  # no retry with another run


def test_check_not_released_tries_the_previous_run_once(monkeypatch, capsys):
    nodata = (400, {}, b'{"message": "Data not released yet for this date"}')
    http = FakePolytope([nodata, nodata])
    assert _check(monkeypatch, http) == 2
    out = json.loads(capsys.readouterr().out)
    assert out["verified"] is None and out["point_forecasts"] is None
    posts = [json.loads(json.loads(b)["request"]) for m, u, h, b in http.calls if m == "POST"]
    assert len(posts) == 2 and (posts[0]["date"], posts[0]["time"]) != (
        posts[1]["date"],
        posts[1]["time"],
    )


def test_check_not_released_then_previous_run_succeeds(monkeypatch, capsys):
    nodata = (400, {}, b'{"message": "no data found"}')
    http = FakePolytope([nodata, accepted()], polls=[ready()])
    assert _check(monkeypatch, http) == 0


@pytest.mark.parametrize("status", [401, 403])
def test_check_key_rejected(monkeypatch, capsys, status):
    http = FakePolytope([(status, {}, b'{"message": "Unauthorized"}')])
    assert _check(monkeypatch, http) == 4
    assert "rejected the key" in json.loads(capsys.readouterr().out)["blocked"]["blocked"]


@pytest.mark.parametrize("status", [429, 500, 503])
def test_check_service_trouble_is_a_network_barrier(monkeypatch, capsys, status):
    http = FakePolytope([(status, {}, b"busy")])
    assert _check(monkeypatch, http) == 2
    assert "cannot reach" in json.loads(capsys.readouterr().out)["blocked"]["blocked"]


def test_check_poll_timeout_is_a_network_barrier_and_deletes_the_job(monkeypatch, capsys):
    clock = iter(range(0, 1000, 7))
    monkeypatch.setattr(pt.time, "monotonic", lambda: next(clock))
    http = FakePolytope([accepted()], polls=[accepted()] * 50)
    assert _check(monkeypatch, http) == 2
    assert ("DELETE", JOB) in [(m, u) for m, u, *_ in http.calls]


def test_check_connection_error_is_a_network_barrier(monkeypatch, capsys):
    def http(method, url, headers, body=None):
        raise OSError("Connection refused")

    assert _check(monkeypatch, http) == 2


def test_check_offline_makes_no_data_request(monkeypatch, capsys):
    def http(*a, **k):
        raise AssertionError("--offline must not submit a request")

    assert _check(monkeypatch, http, offline=True) == 0
    assert json.loads(capsys.readouterr().out)["verified_by"] == "collection_list"


def test_insufficient_permissions_barrier():
    b = pt.barrier_for_error("HTTP 400: insufficient permissions")
    assert "entitlement" in b["blocked"]


def test_fetch_latest_stops_on_insufficient_permissions(monkeypatch):
    tried = []

    def fetch(req):
        tried.append(req["date"])
        raise RuntimeError("Your request could not be processed: insufficient permissions.")

    monkeypatch.setattr(pt, "fetch", fetch)
    with pytest.raises(RuntimeError, match="insufficient permissions"):
        pt.fetch_latest(1, 2, 24, False)
    assert len(tried) == 1


def test_fetch_latest_maps_the_client_crash_on_an_expired_key(monkeypatch):
    def fetch(req):
        raise TypeError("__class__ must be set to a class")

    monkeypatch.setattr(pt, "fetch", fetch)
    with pytest.raises(RuntimeError) as e:
        pt.fetch_latest(1, 2, 24, False)
    assert pt.barrier_for_error(str(e.value))["blocked"] == "Polytope rejected the key"


def test_requests_never_carry_a_list_grid():
    # Polytope rejects grid lists with equal values ("Duplicate values found in list").
    for ens in (False, True):
        assert "grid" not in pt.build_request(1, 2, "20261001", "0000", 24, ens)
# --- feels-like indices (thermofeel) -------------------------------------------------------------


def _with_dewpoint(d: dict, ensemble: bool = False) -> dict:
    """Parsed covjson plus a 2 m dewpoint (and wind for ENS members) 5 K below 2t."""
    p = pt.parse_covjson(d)
    for m in p["members"].values():
        m["2d"] = [v - 5.0 for v in m["2t"]]
        if ensemble:
            m["10u"] = [3.0] * len(m["2t"])
            m["10v"] = [-1.0] * len(m["2t"])
    return p


def test_build_request_indices_add_dewpoint_and_wind():
    r = pt.build_request(38.72, -9.14, "20261001", "0000", 48, indices=True)
    assert r["param"] == "167/228/165/166/151/164/168"
    e = pt.build_request(38.72, -9.14, "20261001", "0000", 48, ensemble=True, indices=True)
    assert e["param"] == "167/228/168/165/166"


@pytest.mark.earthkit
def test_feels_like_is_the_same_as_open_data():
    import numpy as np

    odp = load_script("ecmwf-open-data", "odpoint")
    args = (np.array([310.0, 263.15]), np.array([280.0, 258.15]), np.array([2.0, 5.0]))
    a = pt.feels_like(*args, np.zeros(2))
    b = odp.feels_like(*args, np.zeros(2))
    assert a.keys() == b.keys()
    for k in a:
        np.testing.assert_array_equal(a[k], b[k])


@pytest.mark.earthkit
def test_series_deterministic_indices():
    rows = pt.series(_with_dewpoint(OPER))
    r = rows[6]
    for k in ("heat_index_C", "humidex_C", "apparent_temperature_C", "wind_chill_C"):
        assert k in r
    assert -40 < r["apparent_temperature_C"] < 50


@pytest.mark.earthkit
def test_series_ensemble_index_percentiles():
    rows = pt.series(_with_dewpoint(ENFO, ensemble=True))
    r = rows[6]
    assert r["apparent_temperature_C_p10"] <= r["apparent_temperature_C_p50"]
    assert r["apparent_temperature_C_p50"] <= r["apparent_temperature_C_p90"]
    assert r["apparent_temperature_C"] == r["apparent_temperature_C_p50"]
    assert r["heat_index_C"] == r["heat_index_C_p50"]
