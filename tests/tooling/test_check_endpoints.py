# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""The ECMWF endpoints the skills depend on must still exist (run before every release)."""

import json

import check_endpoints as ce
import pytest
from conftest import FIXTURES

STATUS = json.loads((FIXTURES / "ecmwf-status.json").read_text())
SESSIONS = json.loads((FIXTURES / "ecmwf-status-sessions.json").read_text())
ROUTER_STATUS = json.loads((FIXTURES / "mcp" / "router-status.json").read_text())
MCP_STATUS = "https://mcp.ecmwf.int/status"


INITIALIZE_RESULT = {
    "jsonrpc": "2.0",
    "id": 1,
    "result": {
        "protocolVersion": "2025-06-18",
        "capabilities": {"tools": {}},
        "serverInfo": {"name": "charts", "version": "1.0"},
    },
}
INITIALIZE_JSON = json.dumps(INITIALIZE_RESULT)
INITIALIZE_SSE = f"event: message\ndata: {INITIALIZE_JSON}\n\n"
STATIONINFO = [{"icaoId": "LPPT", "wmoId": "08579", "site": "Lisboa", "lat": 38.77, "lon": -9.13}]


def fetcher(overrides=None):
    def fetch(url, **request):
        for key, value in (overrides or {}).items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        if url in ce.mcp.BACKENDS.values():
            return 200, INITIALIZE_JSON
        if url == ce.NOAA_PROBE:
            return 200, json.dumps(STATIONINFO)
        if url == ce.st.STATUS:
            return 200, json.dumps(STATUS)
        if url == ce.st.SESSIONS:
            return 200, json.dumps(SESSIONS)
        if url == MCP_STATUS:
            return 200, json.dumps(ROUTER_STATUS)
        if url.endswith("/api/catalogue/v1/messages"):
            return 200, '{"messages": []}'
        return 200, "ok"

    return fetch


def test_all_present():
    assert ce.check(fetch=fetcher()) == []


def test_missing_component_is_reported():
    d = json.loads(json.dumps(STATUS))
    d["nodes"] = [n for n in d["nodes"] if n["node"]["Title"] != "WebMARS"]
    probs = ce.check(fetch=fetcher({"/status/status": (200, json.dumps(d))}))
    assert any("WebMARS" in p for p in probs)


def test_status_schema_change_is_reported():
    probs = ce.check(fetch=fetcher({"/status/status": (200, '{"components": []}')}))
    assert any("status" in p and "nodes" in p for p in probs)


def test_datastore_messages_endpoint_gone():
    probs = ce.check(fetch=fetcher({"ecds.ecmwf.int/api/catalogue": (404, "not found")}))
    assert any("ecds.ecmwf.int" in p and "404" in p for p in probs)


def test_unreachable_host_is_reported_but_auth_answers_are_fine():
    probs = ce.check(fetch=fetcher({"api.ecmwf.int": (403, "Missing access token")}))
    assert probs == []
    probs = ce.check(fetch=fetcher({"polytope.ecmwf.int": OSError("Name or service not known")}))
    assert any("polytope.ecmwf.int" in p for p in probs)


def test_every_service_has_a_host_probe():
    assert set(ce.st.SERVICES) <= set(ce.PROBES)
    assert ce.PROBES["mcp"] == MCP_STATUS


def test_mcp_router_moving_a_service_is_reported():
    moved = json.loads(json.dumps(ROUTER_STATUS))
    moved["services"][0]["url"] = "https://charts2.mcp.ecmwf.int/mcp"
    moved["services"].append({"name": "observations", "url": "https://obs.mcp.ecmwf.int/mcp"})
    probs = ce.check(fetch=fetcher({MCP_STATUS: (200, json.dumps(moved))}))
    assert any("charts" in p and "ecmwf_mcp.BACKENDS" in p for p in probs)
    assert any("observations" in p for p in probs)


def test_mcp_router_outage_warns_but_does_not_block_a_release():
    # The plugin's skills work without the MCP (it is an optional addition): an outage is a
    # warning; drift (gone, moved, changed) is still a problem.
    warnings: list[str] = []
    probs = ce.check(
        fetch=fetcher({MCP_STATUS: (502, "<title>Service not available</title>")}),
        warnings=warnings,
    )
    assert not any("mcp" in p for p in probs), probs
    assert any("mcp.ecmwf.int/status" in w and "502" in w for w in warnings)


def test_mcp_router_gone_is_a_problem():
    probs = ce.check(fetch=fetcher({MCP_STATUS: (404, "not found")}))
    assert any("mcp.ecmwf.int/status" in p and "404" in p for p in probs)


def test_mcp_host_that_no_longer_resolves_is_a_problem():
    import socket
    import urllib.error

    def fetch(url, **request):
        if "mcp.ecmwf.int" in url:
            raise urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
        return fetcher({})(url, **request)

    probs = ce.check(fetch=fetch)
    assert any("mcp" in p and "resolve" in p for p in probs), probs


def test_required_service_outage_still_blocks():
    probs = ce.check(fetch=fetcher({"data.ecmwf.int": (502, "")}))
    assert any("data.ecmwf.int" in p and "502" in p for p in probs), probs


def test_destine_catalogue_probe_is_the_one_the_skill_uses():
    from conftest import load_script

    destine = load_script("ecmwf-destine", "destine")
    assert ce.PROBES["destine-catalogue"] == f"{destine.CATALOGUE_URL}/health"


# --- optional services probed with their own request: the MCP backends and NOAA -----------------

OPTIONAL_SERVICES = {
    "mcp-charts": (ce.mcp.BACKENDS["charts"], INITIALIZE_JSON),
    "mcp-catalogue": (ce.mcp.BACKENDS["catalogue"], INITIALIZE_JSON),
    "noaa": (ce.NOAA_PROBE, json.dumps(STATIONINFO)),
}
# One malformed answer per service: HTTP 200, but not what the skills' scripts parse.
CHANGED_SHAPE = {
    "mcp-charts": [
        "<html>Welcome</html>",
        json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "bad"}}),
        json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"unrelated": True}}),
        "event: message\ndata: not json\n\n",
        "",
    ],
    "noaa": [
        "<html>Welcome</html>",
        "[]",
        json.dumps({"data": STATIONINFO}),
        json.dumps([{"station": "LPPT"}]),
        json.dumps([{"icaoId": "EGLL"}]),
        "",
    ],
}
CHANGED_SHAPE["mcp-catalogue"] = CHANGED_SHAPE["mcp-charts"]


def one_service(name, answer):
    url, _ = OPTIONAL_SERVICES[name]
    return fetcher({url: answer})


@pytest.mark.parametrize("name", OPTIONAL_SERVICES)
def test_optional_service_answering_properly_is_fine(name):
    warnings: list[str] = []
    assert ce.check(fetch=fetcher(), warnings=warnings) == []
    assert not any(name in w for w in warnings)


def test_mcp_answer_may_be_an_event_stream():
    fetch = fetcher({ce.mcp.BACKENDS["charts"]: (200, INITIALIZE_SSE)})
    assert ce.check(fetch=fetch) == []


@pytest.mark.parametrize("name", OPTIONAL_SERVICES)
@pytest.mark.parametrize("code", [404, 410])
def test_optional_service_gone_is_a_problem(name, code):
    probs = ce.check(fetch=one_service(name, (code, "")), warnings=[])
    assert any(name in p and str(code) in p for p in probs), probs


@pytest.mark.parametrize("name", OPTIONAL_SERVICES)
@pytest.mark.parametrize("code", [500, 502, 503])
def test_optional_service_outage_warns_but_does_not_block(name, code):
    warnings: list[str] = []
    probs = ce.check(fetch=one_service(name, (code, "")), warnings=warnings)
    assert not any(name in p for p in probs), probs
    assert any(name in w and str(code) in w for w in warnings), warnings


@pytest.mark.parametrize("name", OPTIONAL_SERVICES)
def test_optional_service_timeout_warns_but_does_not_block(name):
    warnings: list[str] = []
    probs = ce.check(fetch=one_service(name, TimeoutError("timed out")), warnings=warnings)
    assert not any(name in p for p in probs), probs
    assert any(name in w and "TimeoutError" in w for w in warnings), warnings


@pytest.mark.parametrize("name", OPTIONAL_SERVICES)
def test_optional_service_that_no_longer_resolves_is_a_problem(name):
    import socket
    import urllib.error

    gone = urllib.error.URLError(socket.gaierror(-2, "Name or service not known"))
    warnings: list[str] = []
    probs = ce.check(fetch=one_service(name, gone), warnings=warnings)
    assert any(name in p and "resolve" in p for p in probs), probs
    assert not any(name in w for w in warnings)


@pytest.mark.parametrize(
    ("name", "body"), [(n, b) for n, bodies in CHANGED_SHAPE.items() for b in bodies]
)
def test_optional_service_changing_its_answer_is_a_problem(name, body):
    probs = ce.check(fetch=one_service(name, (200, body)), warnings=[])
    assert any(name in p for p in probs), (body, probs)


@pytest.mark.parametrize("name", OPTIONAL_SERVICES)
def test_optional_service_throttling_is_retried(name):
    url, body = OPTIONAL_SERVICES[name]
    calls = {"n": 0}
    base = fetcher()

    def throttled(u, **request):
        if u == url:
            calls["n"] += 1
            if calls["n"] == 1:
                return 429, ""
            return 200, body
        return base(u, **request)

    warnings: list[str] = []
    assert ce.check(fetch=ce.retrying(throttled, delay=0), warnings=warnings) == []
    assert calls["n"] == 2
    assert not any(name in w for w in warnings)


@pytest.mark.parametrize("name", OPTIONAL_SERVICES)
def test_optional_service_dropped_connection_is_retried(name):
    url, body = OPTIONAL_SERVICES[name]
    calls = {"n": 0}
    base = fetcher()

    def flaky(u, **request):
        if u == url:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionResetError("reset by peer")
            return 200, body
        return base(u, **request)

    warnings: list[str] = []
    assert ce.check(fetch=ce.retrying(flaky, delay=0), warnings=warnings) == []
    assert calls["n"] == 2
    assert not any(name in w for w in warnings)


def test_mcp_backends_get_an_initialize_request_that_accepts_json_and_event_streams():
    seen = {}
    base = fetcher()

    def spy(url, **request):
        seen[url] = request
        return base(url, **request)

    assert ce.check(fetch=spy, warnings=[]) == []
    for url in ce.mcp.BACKENDS.values():
        message = json.loads(seen[url]["data"])
        assert message["method"] == "initialize"
        assert message["jsonrpc"] == "2.0"
        assert message["params"]["protocolVersion"] == ce.mcp.PROTOCOL
        headers = seen[url]["headers"]
        assert headers["Accept"] == "application/json, text/event-stream"
        assert headers["Content-Type"] == "application/json"


def test_noaa_probe_is_the_request_obs_makes():
    from conftest import load_script

    obs = load_script("ecmwf-observations", "obs")
    asked = []

    def fetch(url, headers=None):
        asked.append((url, headers))
        return STATIONINFO

    obs.noaa_station_info("LPPT", fetch=fetch)
    obs_url, obs_headers = asked[0]
    assert obs_url == ce.NOAA_PROBE

    seen = {}
    base = fetcher()

    def spy(url, **request):
        seen[url] = request
        return base(url, **request)

    ce.check(fetch=spy, warnings=[])
    assert seen[ce.NOAA_PROBE].get("data") is None
    assert seen[ce.NOAA_PROBE]["headers"] == obs_headers


def test_service_endpoints_are_the_urls_the_scripts_use():
    assert set(ce.SERVICE_ENDPOINTS) == {
        *ce.mcp.BACKENDS.values(),
        "https://aviationweather.gov/api/data",
    }
    assert set(ce.probed_urls()) >= set(ce.mcp.BACKENDS.values())
    assert ce.NOAA_PROBE.startswith("https://aviationweather.gov/api/data/stationinfo?ids=")


@pytest.mark.live
def test_live_endpoints_exist():
    assert ce.check() == []


def test_transient_failures_are_retried():
    calls = {"n": 0}
    base = fetcher()

    def flaky(url, **request):
        if url == "https://polytope.ecmwf.int/api/v1/test":
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("timed out")
        return base(url, **request)

    assert ce.check(fetch=ce.retrying(flaky, delay=0)) == []
    assert calls["n"] == 2


def test_rate_limited_answers_are_retried():
    # data.ecmwf.int answers HTTP 429 to bursts of requests, as right after the live tests:
    # a throttled answer is not drift, so the gate asks again before reporting it.
    calls = {"n": 0}
    base = fetcher()

    def throttled(url, **request):
        if url == ce.PROBES["open-data"]:
            calls["n"] += 1
            if calls["n"] < ce.RETRIES:
                return 429, ""
        return base(url, **request)

    assert ce.check(fetch=ce.retrying(throttled, delay=0)) == []
    assert calls["n"] == ce.RETRIES


def test_a_lasting_rate_limit_is_still_reported():
    calls = {"n": 0}
    base = fetcher()

    def throttled(url, **request):
        if url == ce.PROBES["open-data"]:
            calls["n"] += 1
            return 429, ""
        return base(url, **request)

    probs = ce.check(fetch=ce.retrying(throttled, delay=0))
    assert any("data.ecmwf.int" in p and "429" in p for p in probs), probs
    assert calls["n"] == ce.RETRIES


def test_other_answers_are_not_retried():
    calls = {"n": 0}
    base = fetcher()

    def gone(url, **request):
        if url == ce.PROBES["open-data"]:
            calls["n"] += 1
            return 404, ""
        return base(url, **request)

    probs = ce.check(fetch=ce.retrying(gone, delay=0))
    assert any("data.ecmwf.int" in p and "404" in p for p in probs), probs
    assert calls["n"] == 1
