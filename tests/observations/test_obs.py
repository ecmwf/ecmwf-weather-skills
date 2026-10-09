# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""ecmwf-observations obs.py — offline: stations, MARS requests, NOAA labelling, pairing, CLI."""

import ast
import json
import re
import subprocess
import sys
from datetime import UTC, date, datetime

import pytest
from conftest import FIXTURES, ROOT, SKILLS, load_script

obs = load_script("ecmwf-observations", "obs")
SCRIPT = SKILLS / "ecmwf-observations" / "scripts" / "obs.py"
NOW = datetime(2026, 10, 9, 22, 0, tzinfo=UTC)


def noaa(routes):
    """Injected JSON fetcher: URL substring -> parsed body (None = HTTP 204, no content)."""
    calls = []

    def fetch(url, headers=None):
        calls.append((url, headers or {}))
        for key, value in routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise AssertionError(f"unexpected {url}")

    fetch.calls = calls
    return fetch


STATIONINFO = json.loads((FIXTURES / "obs-noaa-stationinfo.json").read_text())
METAR = json.loads((FIXTURES / "obs-noaa-metar-egll.json").read_text())


# --- stations ------------------------------------------------------------------------------------


def test_identifiers_are_classified():
    assert obs.classify("08535") == ("wmo", "08535")
    assert obs.classify("8535") == ("wmo", "08535")
    assert obs.classify("lppt") == ("icao", "LPPT")
    assert obs.classify("Lisboa") == ("name", "Lisboa")
    assert obs.statid("08535") == "   08535" and obs.statid("LPPT") == "    LPPT"


def test_bundled_station_index_finds_lisbon():
    stations = obs.load_stations()
    assert len(stations) > 5000  # every WMO land station OSCAR lists as reporting
    hit = obs.search_stations("08535", stations)[0]
    assert hit["wmo"] == "08535" and "LISBOA" in hit["name"]
    assert abs(hit["lat"] - 38.72) < 0.05 and abs(hit["lon"] + 9.15) < 0.05
    names = [s["wmo"] for s in obs.search_stations("lisboa", stations)]
    assert "08535" in names and "08536" in names


def test_icao_resolves_to_wmo_through_noaa_station_metadata():
    fetch = noaa({"stationinfo": [s for s in STATIONINFO if s["icaoId"] == "LPPT"]})
    st = obs.resolve_station("LPPT", stations=[], fetch=fetch)
    assert st["wmo"] == "08536" and st["icao"] == "LPPT"
    assert st["source"].startswith(obs.NOAA_LABEL)
    url, headers = fetch.calls[0]
    assert url.startswith("https://aviationweather.gov/api/data/stationinfo?ids=LPPT")
    assert "User-Agent" in headers


def test_unknown_icao_is_a_lookup_error():
    with pytest.raises(LookupError):
        obs.resolve_station("ZZZZ", stations=[], fetch=noaa({"stationinfo": None}))


def test_wmo_id_missing_from_index_falls_back_to_oscar():
    oscar = json.loads((FIXTURES / "obs-oscar-synopland.json").read_text())
    one = {"stationSearchResults": oscar["stationSearchResults"][:1]}
    fetch = noaa({"oscar.wmo.int": one})
    st = obs.resolve_station("10384", stations=[], fetch=fetch)
    assert st["wmo"] == "10384" and "OSCAR" in st["source"]
    assert "wigosId=0-20000-0-10384" in fetch.calls[0][0]


def test_noaa_unreachable_is_reported_not_substituted():
    fetch = noaa({"stationinfo": OSError("Connection refused")})
    with pytest.raises(obs.NoaaUnavailable):
        obs.resolve_station("LPPT", stations=[], fetch=fetch)
    b = obs.noaa_barrier("Connection refused")
    assert "aviationweather.gov" in b["why"] and "WMO" in " ".join(b["user_steps"])


# --- MARS requests --------------------------------------------------------------------------------


def test_feedback_request_for_a_synop_station():
    st = {"wmo": "08535", "icao": None, "lat": 38.72, "lon": -9.15}
    reqs = obs.build_requests(st, date(2026, 10, 1), date(2026, 10, 7), ["t2m", "precip"])
    assert len(reqs) == 1
    r = reqs[0]
    assert r["type"] == "ofb" and r["format"] == "odb" and r["time"] == "00/06/12/18"
    assert r["date"] == "20261001/to/20261007"
    assert r["reportype"] == "16001/16002/16076"
    assert "statid@hdr = '   08535'" in r["filter"]
    assert re.search(r"varno@body in \(39,79,80\)", r["filter"])
    assert r["filter"].startswith('"select ') and r["filter"].endswith('"')
    assert obs.lint_request(r) == []


def test_metar_only_station_uses_the_icao_and_metar_report_types():
    st = {"wmo": None, "icao": "LPPT", "lat": 38.78, "lon": -9.14}
    r = obs.build_requests(st, date(2026, 10, 8), date(2026, 10, 8), ["t2m"])[0]
    assert r["reportype"] == "16004/16065" and "'    LPPT'" in r["filter"]


def test_long_periods_are_split_into_weekly_requests():
    st = {"wmo": "08535", "icao": None, "lat": 38.72, "lon": -9.15}
    reqs = obs.build_requests(st, date(2026, 9, 1), date(2026, 9, 30), ["t2m"])
    assert len(reqs) == 5 and reqs[0]["date"] == "20260901/to/20260907"
    assert reqs[-1]["date"] == "20260929/to/20260930"


def test_raw_bufr_requests():
    st = {"wmo": "08535", "icao": None, "lat": 38.72, "lon": -9.15}
    r = obs.build_requests(st, date(2026, 10, 1), date(2026, 10, 7), ["t2m"], raw=True)[0]
    assert r["type"] == "ob" and r["obstype"] == "lsd" and r["ident"] == "08535"
    assert r["time"] == "00/03/06/09/12/15/18/21" and r["range"] == "179"
    assert obs.lint_request(r) == []
    metar = {"wmo": None, "icao": "LPPT", "lat": 38.78, "lon": -9.14}
    m = obs.build_requests(metar, date(2026, 10, 7), date(2026, 10, 7), ["t2m"], raw=True)[0]
    assert m["obstype"] == "metar" and "ident" not in m
    n, w, s, e = (float(x) for x in m["area"].split("/"))
    assert n > 38.78 > s and w < -9.14 < e


def test_mars_text_keeps_the_quoted_filter():
    st = {"wmo": "08535", "icao": None, "lat": 38.72, "lon": -9.15}
    r = obs.build_requests(st, date(2026, 10, 9), date(2026, 10, 9), ["t2m"])[0]
    text = obs.mars_text(r)
    assert text.startswith("retrieve,") and f"filter={r['filter']}" in text


def test_lint_request_catches_mistakes():
    st = {"wmo": "08535", "icao": None, "lat": 38.72, "lon": -9.15}
    r = obs.build_requests(st, date(2026, 10, 9), date(2026, 10, 9), ["t2m"])[0]
    assert obs.lint_request({**r, "format": "grib"})
    assert obs.lint_request({**r, "filter": '"select statid,obsvalue@body"'})
    assert obs.lint_request({**r, "date": "20261009/20261010"})  # two days, not a range


def test_variables():
    assert obs.parse_vars("t2m,wind") == ["t2m", "wind_speed", "wind_dir"]
    assert "precip" in obs.parse_vars(None)
    with pytest.raises(ValueError, match="unknown"):
        obs.parse_vars("t2m,visibility")


def test_latency_notes():
    assert obs.latency_note(date(2026, 10, 8), raw=False, now=NOW) is None
    assert "10 h" in obs.latency_note(date(2026, 10, 9), raw=False, now=NOW)  # 18 UTC window
    assert "2 days" in obs.latency_note(date(2026, 10, 8), raw=True, now=NOW)


def test_missing_values_and_qc_flags():
    assert obs.is_missing(-3.4028234663852886e38) and obs.is_missing(-1e100)
    assert obs.is_missing(None) and obs.is_missing(float("nan"))
    assert not obs.is_missing(0.0) and not obs.is_missing(-40.0)
    assert obs.qc_label(1) == "active"
    assert obs.qc_label(12) == "rejected+blacklisted"
    assert obs.qc_label(None) == ""


# --- NOAA METAR (labelled, non-ECMWF) -------------------------------------------------------------


def test_latest_metar_rows_are_labelled_and_converted():
    rows = obs.noaa_latest_metar("EGLL", hours=3, fetch=noaa({"/metar": METAR}))
    assert rows and all(r["source"] == obs.NOAA_LABEL for r in rows)
    newest = max(r["time_utc"] for r in rows)
    assert newest == "2026-10-09T22:20Z"
    t = next(r for r in rows if r["variable"] == "t2m" and r["time_utc"] == newest)
    assert t["value"] == 12 and t["units"] == "degC"
    w = next(r for r in rows if r["variable"] == "wind_speed" and r["time_utc"] == newest)
    assert w["value"] == pytest.approx(11 * 1852 / 3600, abs=0.01) and w["units"] == "m/s"
    raw = next(r for r in rows if r["variable"] == "raw" and r["time_utc"] == newest)
    assert raw["value"].startswith("METAR EGLL")


def test_only_obs_py_contacts_aviationweather_and_only_through_labelled_functions():
    # ECMWF sources only, with one scoped exception (AGENTS.md): station metadata and the
    # labelled latest METAR from NOAA Aviation Weather, only in obs.py.
    for f in (ROOT / "plugins").rglob("*.py"):
        if "aviationweather" in f.read_text():
            assert f == SCRIPT, f
    tree = ast.parse(SCRIPT.read_text())
    users = {
        node.name
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and any(isinstance(n, ast.Name) and n.id == "NOAA_API" for n in ast.walk(node))
    }
    assert users == {"noaa_station_info", "noaa_latest_metar"}


# --- verification ---------------------------------------------------------------------------------


def forecast():
    return {
        "run": "2026-10-08T00:00Z",
        "model": "ifs",
        "units": {"t2m_C": "degC", "wind_speed_ms": "m/s"},
        "series": [
            {"step": 0, "valid_time": "2026-10-08T00:00Z", "t2m_C": 18.0, "wind_speed_ms": 3.0},
            {"step": 3, "valid_time": "2026-10-08T03:00Z", "t2m_C": 17.0, "wind_speed_ms": 4.0},
            {"step": 6, "valid_time": "2026-10-08T06:00Z", "t2m_C": 16.0, "wind_speed_ms": 5.0},
        ],
    }


def observed():
    def row(t, var, v):
        return {"time_utc": t, "variable": var, "value": v, "source": obs.ECMWF_LABEL}

    return [
        row("2026-10-08T00:00Z", "t2m", 17.0),
        row("2026-10-08T03:10Z", "t2m", 18.0),  # within 30 min: paired with 03:00
        row("2026-10-08T06:00Z", "wind_speed", 4.0),
        row("2026-10-08T07:00Z", "t2m", 12.0),  # no forecast at 07: ignored
    ]


def test_pairs_by_valid_time_with_a_tolerance():
    pairs = obs.pair(forecast(), observed())
    got = {(p["step"], p["variable"]): (p["forecast"], p["observed"]) for p in pairs}
    assert got == {(0, "t2m"): (18.0, 17.0), (3, "t2m"): (17.0, 18.0), (6, "wind_speed"): (5, 4)}


def test_merged_point_json_carries_observations_for_the_meteogram():
    merged = obs.with_observations(forecast(), observed(), station={"wmo": "08535"})
    o = merged["observations"]
    assert o["source"] == obs.ECMWF_LABEL and o["station"]["wmo"] == "08535"
    assert {"valid_time": "2026-10-08T00:00Z", "t2m_C": 17.0} in o["series"]
    assert merged["series"] == forecast()["series"]
    assert "observations: © ECMWF" in merged["attribution"]["short"]


def test_pairs_csv(tmp_path):
    out = tmp_path / "pairs.csv"
    obs.write_csv(obs.pair(forecast(), observed()), out)
    head = out.read_text().splitlines()[0].split(",")
    assert head[:4] == ["valid_time", "step", "variable", "forecast"]


# --- MARS errors -> BLOCKED -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "ecmwfapi.api.APIException: 'ecmwf.API error 1: User xyz has no access to TYPE=OB'",
        "MARS ERROR: restricted access to observations (TYPE=OFB)",
        "403 Client Error: Forbidden",
    ],
)
def test_no_observation_rights_is_an_access_barrier(message):
    b = obs.barrier_for_error(message)
    assert b and "Computing Representative" in " ".join(b["user_steps"])
    assert "--latest-metar" in b["agent"] and "ask" in b["agent"].lower()


def test_unreachable_web_api_reports_ecmwf_status(monkeypatch):
    status = json.loads((FIXTURES / "ecmwf-status.json").read_text())
    monkeypatch.setattr(
        obs.ecmwf_status, "_get", lambda url: status if "/status/status" in url else {"nodes": []}
    )
    b = obs.barrier_for_error("HTTPSConnectionPool: Max retries exceeded (Connection refused)")
    assert b["blocked"].startswith("cannot reach") and "ECMWF status" in b["why"]


def test_interrupted_retrieval_deletes_the_server_job(monkeypatch, tmp_path):
    import types

    deleted = []

    class Connection:
        def __init__(self, *a, **k):
            self.location = "https://api.ecmwf.int/v1/services/mars/requests/obs1"

        def cleanup(self):
            deleted.append(self.location)

    class Service:
        def __init__(self, *a, **k):
            pass

        def execute(self, req, target):
            Connection()
            raise KeyboardInterrupt

    api = types.ModuleType("ecmwfapi.api")
    api.Connection = Connection
    pkg = types.ModuleType("ecmwfapi")
    pkg.api = api
    pkg.ECMWFService = Service
    monkeypatch.setitem(sys.modules, "ecmwfapi", pkg)
    monkeypatch.setitem(sys.modules, "ecmwfapi.api", api)
    with pytest.raises(KeyboardInterrupt):
        obs.mars_retrieve({"class": "od"}, tmp_path / "x.odb")
    assert deleted == ["https://api.ecmwf.int/v1/services/mars/requests/obs1"]
    assert Connection.__init__.__name__ == "__init__"


# --- CLI ------------------------------------------------------------------------------------------


def run(*args, home=None, env=None):
    import os

    e = {**os.environ, **(env or {})}
    if home:
        e["HOME"] = str(home)
        for k in ("ECMWF_API_KEY", "ECMWF_API_URL", "ECMWF_API_EMAIL", "ECMWF_API_RC_FILE"):
            e.pop(k, None)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=e, timeout=120
    )


def test_cli_stations_offline_by_wmo_and_name():
    out = run("stations", "08535", "--json")
    assert out.returncode == 0, out.stderr
    hits = json.loads(out.stdout)["stations"]
    assert hits[0]["wmo"] == "08535" and hits[0]["source"].startswith("WMO OSCAR")
    out = run("stations", "lisboa")
    assert "08536" in out.stdout


def test_cli_series_dry_run_prints_lint_clean_requests():
    out = run(
        "series",
        "--station",
        "08535",
        "--start",
        "2026-10-01",
        "--end",
        "2026-10-07",
        "--dry-run",
    )
    assert out.returncode == 0, out.stderr
    assert "type=ofb" in out.stdout and "statid@hdr = '   08535'" in out.stdout


def test_cli_series_without_credentials_is_blocked(tmp_path):
    args = ("series", "--station", "08535", "--start", "2026-10-01", "--end", "2026-10-02")
    out = run(*args, home=tmp_path)
    assert out.returncode == 4
    assert "BLOCKED" in out.stderr and "api.ecmwf.int/v1/key" in out.stderr


def test_cli_rejects_reversed_dates():
    out = run("series", "--station", "08535", "--start", "2026-10-07", "--end", "2026-10-01")
    assert out.returncode == 2 and "start" in out.stderr


def test_retrieved_rows_are_trimmed_to_the_requested_days(monkeypatch):
    # the 00 UTC feedback window starts at 21 UTC the day before
    def row(t, var="t2m"):
        return {"station": "08535", "time_utc": t, "variable": var, "value": 1.0}

    monkeypatch.setattr(obs, "mars_retrieve", lambda req, target: 10)
    monkeypatch.setattr(
        obs,
        "decode_feedback",
        lambda path, sts: [
            row("2026-10-07T22:00Z"),
            row("2026-10-08T00:00Z"),
            row("2026-10-08T00:00Z", "td"),
            row("2026-10-09T23:00Z"),
            row("2026-10-10T00:00Z"),
        ],
    )
    st = {"wmo": "08535", "icao": None, "lat": 38.72, "lon": -9.15}
    rows = obs.fetch_observations(
        [st], date(2026, 10, 8), date(2026, 10, 9), ["t2m"], False, "auto", log=lambda m: None
    )
    assert [r["time_utc"] for r in rows] == ["2026-10-08T00:00Z", "2026-10-09T23:00Z"]


def test_cli_points_picks_each_station_nearest_the_time(tmp_path):
    series = {
        "source": obs.ECMWF_LABEL,
        "stations": {"08535": {"name": "LISBOA", "lat": 38.72, "lon": -9.15}},
        "rows": [
            {"station": "08535", "time_utc": "2026-10-09T05:00Z", "variable": "t2m", "value": 1},
            {"station": "08535", "time_utc": "2026-10-09T06:10Z", "variable": "t2m", "value": 2},
            {"station": "08535", "time_utc": "2026-10-09T06:00Z", "variable": "td", "value": 3},
        ],
    }
    (tmp_path / "s.json").write_text(json.dumps(series))
    out = tmp_path / "p.json"
    rc = obs.main(
        ["points", str(tmp_path / "s.json"), "--time", "2026-10-09T06:00Z", "-o", str(out)]
    )
    assert rc == 0
    p = json.loads(out.read_text())
    assert p["units"] == "degC" and p["source"] == obs.ECMWF_LABEL
    assert [(x["station"], x["value"]) for x in p["points"]] == [("08535", 2)]
