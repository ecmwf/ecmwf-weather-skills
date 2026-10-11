# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""ecmwf_mcp.py — the stdlib client for ECMWF's MCP server, for agents without MCP tools.

Every exchange is replayed from JSON-RPC responses recorded from mcp.ecmwf.int and its
services (tests/fixtures/mcp/); the transport is injected, so no test touches the network."""

import base64
import json
import subprocess
import sys
import urllib.error
from datetime import datetime, timezone

import pytest
from conftest import FIXTURES, SKILLS, load_script

m = load_script("ecmwf-open-data", "ecmwf_mcp")
MCP = FIXTURES / "mcp"
ROUTER = m.ROUTER
CHARTS = m.BACKENDS["charts"]
CATALOGUE = m.BACKENDS["catalogue"]
NOW = datetime(2026, 10, 11, 6, 0, tzinfo=timezone.utc)
COPIES = ("ecmwf-polytope", "ecmwf-cds-ads", "ecmwf-opencharts-wms", "ecmwf-destine")
BOLOGNA = {
    "collection_id": "operational-data",
    "latitude": 44.49,
    "longitude": 11.34,
    "parameters": ["tp"],
}


def fx(name: str) -> dict:
    return json.loads((MCP / name).read_text())


def raw(name: str, ctype: str = "application/json", status: int = 200):
    return status, {"content-type": ctype}, (MCP / name).read_bytes()


DOWN = (502, {"content-type": "text/html"}, (MCP / "router-502.html").read_bytes())


class Server:
    """A fake MCP deployment: URL -> JSON-RPC method -> recorded answer. Logs every message."""

    def __init__(self, routes, gets=None):
        self.routes, self.gets, self.log = routes, gets or {}, []

    def post(self, url, body, headers, timeout):
        msg = json.loads(body)
        self.log.append((url, msg.get("method"), msg.get("params"), dict(headers)))
        route = self.routes.get(url)
        if route is None:
            raise urllib.error.URLError("Name or service not known")
        if isinstance(route, BaseException):
            raise route
        if isinstance(route, tuple):  # one fixed HTTP answer, e.g. a 502 page
            return route
        if "id" not in msg:  # a notification is accepted without a body
            return 202, {}, b""
        answer = route[msg["method"]]
        if callable(answer):
            answer = answer(msg)
        if isinstance(answer, tuple):
            return answer
        return (
            200,
            {"content-type": "application/json"},
            json.dumps(dict(answer, id=msg["id"])).encode(),
        )

    def get(self, url, timeout):
        route = self.gets.get(url)
        if route is None:
            raise urllib.error.URLError("Name or service not known")
        return route

    def methods(self, url=None):
        return [meth for u, meth, _p, _h in self.log if url is None or u == url]

    def calls(self, url=None):
        return [p for u, meth, p, _h in self.log if meth == "tools/call" and (url in (None, u))]


def router(**answers):
    return {"initialize": fx("router-initialize.json"), **answers}


def run(argv, server, capsys, env=None, now=NOW):
    code = m.main(argv, post=server.post, get=server.get, now=now, env=env or {})
    out, err = capsys.readouterr()
    return code, out, err


@pytest.fixture
def status_feeds(monkeypatch):
    """ECMWF's status feeds as recorded, for the BLOCKED reports of outage tests."""
    status = json.loads((FIXTURES / "ecmwf-status.json").read_text())

    def get(url):
        if "/status/status" in url:
            return status
        if url.endswith("mcp.ecmwf.int/status"):
            raise urllib.error.HTTPError(url, 502, "Bad Gateway", None, None)
        return {"nodes": []}

    monkeypatch.setattr(m.ecmwf_status, "_get", get)


# --- calling a tool -----------------------------------------------------------------------------


def test_handshake_then_call_through_the_router(capsys):
    s = Server({ROUTER: router(**{"tools/call": fx("forecast-point-bologna-tp.json")})})
    code, out, _ = run(["call", "forecast_get_point_forecast", json.dumps(BOLOGNA)], s, capsys)
    assert code == 0
    assert s.methods() == ["initialize", "notifications/initialized", "tools/call"]
    assert s.calls()[0] == {"name": "forecast_get_point_forecast", "arguments": BOLOGNA}
    init = s.log[0][2]
    assert init["protocolVersion"] == "2025-06-18" and init["clientInfo"]["name"]
    assert s.log[2][3]["MCP-Protocol-Version"] == "2025-06-18"
    assert "| time (UTC) |" in out and "Model run: 2026-10-10T00:00:00Z" in out


def test_accumulated_precipitation_becomes_the_amount_over_the_window(capsys):
    s = Server({ROUTER: router(**{"tools/call": fx("forecast-point-bologna-tp.json")})})
    _, out, _ = run(["call", "forecast_get_point_forecast", json.dumps(BOLOGNA)], s, capsys)
    assert "accumulated" in out and "1.50 mm" in out
    # summing the column is the classic mistake: 20.92 mm instead of 1.50 mm
    assert "20.9" not in out
    # per-step amounts, grouped by UTC day; steps without precipitation are left out
    assert "2026-10-11 22Z 0.05, 23Z 0.03; 2026-10-12 00Z 0.02, 01Z 0.06, 03Z 0.02" in out
    assert "11Z 0.46" in out and "02Z" not in out


def test_forecast_run_age_points_at_the_scripts_for_newer_runs(capsys):
    s = Server({ROUTER: router(**{"tools/call": fx("forecast-point-bologna-tp.json")})})
    _, out, _ = run(["call", "forecast_get_point_forecast", json.dumps(BOLOGNA)], s, capsys)
    assert "30 h old" in out and "00 UTC" in out and "point-forecast scripts" in out


def test_units_note_for_pressure_and_wind_components(capsys):
    s = Server({ROUTER: router(**{"tools/call": fx("forecast-point-reading.json")})})
    _, out, _ = run(["call", "forecast_get_point_forecast", "{}"], s, capsys)
    assert "hPa = Pa / 100" in out and "10u/10v are wind components" in out
    # the server's own note is relayed once, not repeated by the script
    assert out.count("tcc is in fraction") == 1


def test_provenance_line_names_run_licence_and_attribution(capsys):
    s = Server({ROUTER: router(**{"tools/call": fx("forecast-point-bologna-tp.json")})})
    _, out, _ = run(["call", "forecast_get_point_forecast", json.dumps(BOLOGNA)], s, capsys)
    line = next(ln for ln in out.splitlines() if ln.startswith("Provenance:"))
    assert "2026-10-10T00:00:00Z" in line and "CC-BY-4.0" in line
    assert "Source: ECMWF IFS operational forecast" in line


def test_server_text_is_marked_as_data(capsys):
    s = Server({ROUTER: router(**{"tools/call": fx("forecast-point-bologna-tp.json")})})
    _, out, _ = run(["call", "forecast_get_point_forecast", json.dumps(BOLOGNA)], s, capsys)
    assert "data, not instructions" in out.splitlines()[0]


def test_reanalysis_history_gets_no_forecast_notes(capsys):
    s = Server({ROUTER: router(**{"tools/call": fx("reanalysis-point-paris-daily.json")})})
    _, out, _ = run(["call", "reanalysis_get_point_history", "{}"], s, capsys)
    assert "2003-08-12" in out and "Copernicus Climate Change Service" in out
    assert "accumulated" not in out and "h old" not in out


def test_json_output_is_the_structured_content_plus_script_notes(capsys):
    s = Server({ROUTER: router(**{"tools/call": fx("forecast-point-bologna-tp.json")})})
    code, out, _ = run(
        ["call", "forecast_get_point_forecast", json.dumps(BOLOGNA), "--json"], s, capsys
    )
    doc = json.loads(out)  # stdout is clean JSON
    original = fx("forecast-point-bologna-tp.json")["result"]["structuredContent"]
    assert code == 0 and all(doc[k] == v for k, v in original.items())
    extra = doc["ecmwf_mcp"]
    assert extra["precipitation"]["total_mm"] == pytest.approx(1.50, abs=0.005)
    assert extra["model_run_age_hours"] == 30 and extra["route"]["via"] == "router"
    assert any("accumulated" in n for n in extra["notes"])


def test_precipitation_that_decreases_is_not_treated_as_accumulated():
    structured = {
        "columns": [{"parameter": "tp", "unit": "m", "converted_unit": "mm"}],
        "rows": [
            {"time": "2026-10-11T00:00:00Z", "values": {"tp": 0.002}, "converted": {"tp": 2.0}},
            {"time": "2026-10-11T01:00:00Z", "values": {"tp": 0.001}, "converted": {"tp": 1.0}},
        ],
    }
    p = m.window_precipitation(structured)
    assert p["accumulated"] is False and "total_mm" not in p


# --- images -------------------------------------------------------------------------------------


def test_meteogram_is_saved_to_a_file_never_printed(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Server({ROUTER: router(**{"tools/call": fx("charts-meteogram.json")})})
    args = '{"latitude": 51.4, "longitude": -1.32, "place_name": "Newbury"}'
    code, out, _ = run(["call", "charts_get_meteogram", args, "-o", "newbury.png"], s, capsys)
    data = next(c for c in fx("charts-meteogram.json")["result"]["content"] if c["type"] == "image")
    assert code == 0
    assert (tmp_path / "newbury.png").read_bytes() == base64.b64decode(data["data"])
    assert data["data"][:40] not in out and "newbury.png" in out and "350 x 50" in out


def test_image_without_output_name_gets_a_unique_file_in_the_working_directory(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    s = Server({ROUTER: router(**{"tools/call": fx("charts-meteogram.json")})})
    for _ in range(2):
        run(["call", "charts_get_meteogram", '{"latitude": 1, "longitude": 2}'], s, capsys)
    names = sorted(p.name for p in tmp_path.glob("*.png"))
    assert names == ["charts_get_meteogram-2.png", "charts_get_meteogram.png"]


def test_map_and_legend_are_two_files(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Server({ROUTER: router(**{"tools/call": fx("charts-map.json")})})
    code, out, _ = run(["call", "charts_get_map", "{}", "-o", "mslp.png"], s, capsys)
    assert code == 0 and (tmp_path / "mslp.png").exists() and (tmp_path / "mslp-2.png").exists()
    assert "legend" in out


# --- errors -------------------------------------------------------------------------------------


def test_tool_error_is_relayed_with_exit_2(capsys):
    s = Server({ROUTER: router(**{"tools/call": fx("forecast-error-run.json")})})
    code, _out, err = run(["call", "forecast_get_point_forecast", "{}"], s, capsys)
    assert code == 2 and "isn't available for operational-data" in err


def test_json_rpc_error_exits_2(capsys):
    error = {"jsonrpc": "2.0", "error": {"code": -32602, "message": "Invalid params: latitude"}}
    s = Server({ROUTER: router(**{"tools/call": error})})
    code, _, err = run(["call", "forecast_get_point_forecast", "{}"], s, capsys)
    assert code == 2 and "Invalid params: latitude" in err


def test_arguments_must_be_a_json_object(capsys):
    s = Server({})
    code, _, err = run(["call", "charts_find_charts", "[1, 2]"], s, capsys)
    assert code == 2 and "JSON object" in err and s.log == []


def test_credentials_are_never_sent(capsys):
    s = Server({ROUTER: router()})
    args = '{"dataset_id": "climate-dt", "selection": {}, "destine_token": "x"}'
    code, _, err = run(["call", "catalogue_retrieve_data", args], s, capsys)
    assert code == 2 and "destine_token" in err and "destine.py retrieve" in err
    assert s.log == []


def test_router_down_falls_back_to_the_service_itself(tmp_path, capsys, monkeypatch):
    monkeypatch.chdir(tmp_path)
    s = Server(
        {
            ROUTER: DOWN,
            CHARTS: {
                "initialize": fx("charts-backend-initialize.json"),
                "tools/call": fx("charts-backend-chart.json"),
            },
        }
    )
    args = '{"product": "medium-mslp-wind850", "step": 24, "area": "europe"}'
    code, out, _ = run(["call", "charts_get_chart", args, "-o", "mslp.png"], s, capsys)
    assert code == 0 and (tmp_path / "mslp.png").exists()
    # the service's own tool names have no service prefix
    assert s.calls(CHARTS)[0]["name"] == "get_chart"
    assert "HTTP 502" in out and CHARTS in out and "Valid time: Mon 12 Oct 2026 00 UTC" in out


def test_router_and_service_down_is_a_network_barrier(capsys, status_feeds):
    s = Server({ROUTER: DOWN, CHARTS: DOWN})
    args = '{"product": "medium-mslp-wind850", "step": 24}'
    code, _, err = run(["call", "charts_get_chart", args], s, capsys)
    assert code == 2 and "BLOCKED: cannot reach the ECMWF mcp service" in err
    assert "status.ecmwf.int" in err and "scripts" in err.split("For the agent:")[1]


def test_router_only_tools_have_no_fallback(capsys, status_feeds):
    s = Server({ROUTER: DOWN})
    code, _, err = run(["call", "find_service", '{"question": "rain"}'], s, capsys)
    assert code == 2 and "BLOCKED" in err and s.methods(CHARTS) == []


def test_unknown_host_is_a_network_barrier(capsys, status_feeds):
    s = Server({})  # nothing resolves: no network at all
    code, _, err = run(["call", "charts_find_charts", '{"query": "rain"}'], s, capsys)
    assert code == 2 and "BLOCKED" in err


# --- protocol details ---------------------------------------------------------------------------


def test_event_stream_answers_are_parsed():
    _status, headers, body = raw("catalogue-backend-tools-list.sse", "text/event-stream")
    (msg,) = m.parse_messages(headers["content-type"], body)
    assert msg["id"] == 2 and msg["result"]["tools"][0]["name"] == "list_datasets"


def test_session_id_from_initialize_is_sent_back(capsys):
    def init(msg):
        doc = dict(fx("router-initialize.json"), id=msg["id"])
        return (
            200,
            {"content-type": "application/json", "mcp-session-id": "s-1"},
            json.dumps(doc).encode(),
        )

    s = Server({ROUTER: {"initialize": init, "tools/call": fx("forecast-point-bologna-tp.json")}})
    run(["call", "forecast_get_point_forecast", json.dumps(BOLOGNA)], s, capsys)
    assert all(h.get("Mcp-Session-Id") == "s-1" for _u, _m, _p, h in s.log[1:])


def test_router_url_can_be_overridden_but_only_with_https(capsys):
    other = "https://mcp.example.test/mcp"
    s = Server({other: router(**{"tools/call": fx("charts-backend-error.json")})})
    run(["call", "charts_get_chart", "{}"], s, capsys, env={"ECMWF_MCP_URL": other})
    assert s.methods(other)[0] == "initialize"
    code, _, err = run(
        ["call", "charts_get_chart", "{}"],
        Server({}),
        capsys,
        env={"ECMWF_MCP_URL": "http://mcp.example.test"},
    )
    assert code == 2 and "https://" in err


def test_service_urls_can_be_overridden(capsys):
    charts = "https://charts.example.test/mcp"
    s = Server(
        {
            ROUTER: DOWN,
            charts: {
                "initialize": fx("charts-backend-initialize.json"),
                "tools/call": fx("charts-backend-error.json"),
            },
        }
    )
    run(["call", "charts_get_chart", "{}"], s, capsys, env={"ECMWF_MCP_CHARTS_URL": charts})
    assert s.methods(charts) == ["initialize", "notifications/initialized", "tools/call"]


def test_control_sequences_in_server_text_are_removed(capsys):
    doc = fx("charts-backend-error.json")
    doc["result"]["isError"] = False
    doc["result"]["content"][0]["text"] = "plain \x1b[31mred\x1b[0m text\x07"
    s = Server({ROUTER: router(**{"tools/call": doc})})
    _, out, _ = run(["call", "charts_find_charts", "{}"], s, capsys)
    assert "plain red text" in out and "\x1b" not in out and "\x07" not in out


def test_long_text_is_truncated_with_a_pointer_to_json(capsys):
    doc = fx("charts-backend-error.json")
    doc["result"]["isError"] = False
    doc["result"]["content"][0]["text"] = "row\n" * 20000
    s = Server({ROUTER: router(**{"tools/call": doc})})
    _, out, _ = run(["call", "catalogue_get_axes", "{}"], s, capsys)
    assert len(out) < m.MAX_TEXT + 2000
    note = next(ln for ln in out.splitlines() if "truncated" in ln)
    assert "more characters" in note and "--json" in note


# --- tools and status ---------------------------------------------------------------------------


def test_tools_lists_names_arguments_and_one_line(capsys):
    s = Server({ROUTER: router(**{"tools/list": fx("router-tools-list.json")})})
    code, out, _ = run(["tools", "--service", "charts"], s, capsys)
    lines = [ln for ln in out.splitlines() if ln.startswith("charts_")]
    assert code == 0 and len(lines) == 4
    meteogram = next(ln for ln in lines if ln.startswith("charts_get_meteogram("))
    assert "latitude, longitude, [place_name]" in meteogram
    assert "ensemble meteogram" in meteogram and "[ECMWF charts]" not in meteogram


def test_tools_from_the_services_when_the_router_is_down(capsys):
    s = Server(
        {
            ROUTER: DOWN,
            CATALOGUE: {
                "initialize": lambda msg: raw(
                    "catalogue-backend-initialize.sse", "text/event-stream"
                ),
                "tools/list": lambda msg: raw(
                    "catalogue-backend-tools-list.sse", "text/event-stream"
                ),
            },
        }
    )
    code, out, _ = run(["tools", "--service", "catalogue"], s, capsys)
    assert code == 0 and "catalogue_list_datasets(" in out and "HTTP 502" in out
    assert "listed the tools of its services directly (catalogue)" in out


def test_status_summarises_the_router(capsys):
    s = Server({}, gets={f"{ROUTER}/status": raw("router-status.json")})
    code, out, _ = run(["status"], s, capsys)
    assert code == 0 and "2/2 services reachable" in out and "charts.mcp.ecmwf.int" in out


def test_status_checks_each_service_when_the_router_is_down(capsys):
    s = Server(
        {
            CHARTS: {"initialize": fx("charts-backend-initialize.json")},
            CATALOGUE: DOWN,
        },
        gets={f"{ROUTER}/status": DOWN},
    )
    code, out, _ = run(["status"], s, capsys)
    assert code == 2 and "HTTP 502" in out
    charts = next(ln for ln in out.splitlines() if ln.strip().startswith("charts"))
    catalogue = next(ln for ln in out.splitlines() if ln.strip().startswith("catalogue"))
    assert "answers" in charts and "502" in catalogue


# --- packaging ----------------------------------------------------------------------------------


def test_copies_are_identical():
    canonical = (SKILLS / "ecmwf-open-data" / "scripts" / "ecmwf_mcp.py").read_text()
    for skill in COPIES:
        assert (SKILLS / skill / "scripts" / "ecmwf_mcp.py").read_text() == canonical, skill


def test_backends_cover_every_service_of_the_router():
    status = fx("router-status.json")
    assert {s["name"]: s["url"] for s in status["services"]} == m.BACKENDS


def test_help_runs_offline(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SKILLS / "ecmwf-open-data" / "scripts" / "ecmwf_mcp.py"), "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        cwd=tmp_path,
    )
    assert out.returncode == 0 and "call" in out.stdout and "status" in out.stdout


# --- live ---------------------------------------------------------------------------------------
# The live service may be in an outage; an outage must then be reported the documented way
# (exit 2, BLOCKED report with ECMWF's status), which is behaviour these tests also check.


def _live(*args, cwd):
    script = SKILLS / "ecmwf-open-data" / "scripts" / "ecmwf_mcp.py"
    return subprocess.run(
        [sys.executable, str(script), *args], capture_output=True, text=True, timeout=300, cwd=cwd
    )


@pytest.mark.live
def test_live_status(tmp_path):
    r = _live("status", cwd=tmp_path)
    assert r.returncode in (0, 2) and "ECMWF MCP router" in r.stdout


@pytest.mark.live
def test_live_meteogram_is_a_png(tmp_path):
    r = _live(
        "call",
        "charts_get_meteogram",
        '{"latitude": 51.4, "longitude": -1.32}',
        "-o",
        "m.png",
        cwd=tmp_path,
    )
    assert r.returncode == 0, r.stderr
    assert (tmp_path / "m.png").read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


@pytest.mark.live
def test_live_point_forecast_works_or_is_reported_unavailable(tmp_path):
    # MCP 0.4.0 no longer offers forecast tools; if they return, the notes must be there.
    r = _live("call", "forecast_get_point_forecast", json.dumps(BOLOGNA), cwd=tmp_path)
    if r.returncode == 0:
        assert "Model run" in r.stdout and "accumulated" in r.stdout
    else:
        assert r.returncode == 2, r.stderr
        assert "BLOCKED: cannot reach" in r.stderr or "does not offer" in r.stderr, r.stderr


@pytest.mark.live
def test_live_era5_history_works_or_is_reported_unavailable(tmp_path):
    args = {
        "collection_id": "era5-single-levels",
        "latitude": 48.86,
        "longitude": 2.35,
        "parameters": ["t2m"],
        "start": "2003-08-01T00:00:00Z",
        "end": "2003-08-02T23:00:00Z",
        "daily": True,
    }
    r = _live("call", "reanalysis_get_point_history", json.dumps(args), cwd=tmp_path)
    if r.returncode == 0:
        assert "2003-08-01" in r.stdout and "Copernicus" in r.stdout
    else:
        assert r.returncode == 2, r.stderr
        assert "BLOCKED: cannot reach" in r.stderr or "does not offer" in r.stderr, r.stderr


def test_answer_cut_off_mid_way_counts_as_unavailable(capsys, status_feeds):
    import http.client

    s = Server({ROUTER: http.client.IncompleteRead(b"{"), CHARTS: http.client.IncompleteRead(b"")})
    code, _, err = run(["call", "charts_find_charts", '{"query": "rain"}'], s, capsys)
    assert code == 2 and "BLOCKED" in err


def test_a_tool_the_router_does_not_offer_points_to_the_skill_scripts(capsys):
    # MCP 0.4.0 offers charts and the DestinE catalogue only; asking for a forecast tool must
    # send the agent to the skill's own scripts, not leave it guessing.
    s = Server({ROUTER: router(**{"tools/call": fx("router-no-such-tool.json")})})
    code, _out, err = run(["call", "forecast_get_point_forecast", "{}"], s, capsys)
    assert code == 2
    assert "does not offer forecast_get_point_forecast" in err
    assert "skill's scripts" in err and "charts_" in err


def test_backends_are_the_services_the_router_lists():
    status = json.loads((FIXTURES / "mcp" / "router-status.json").read_text())
    assert {s["name"]: s["url"] for s in status["services"]} == m.BACKENDS
