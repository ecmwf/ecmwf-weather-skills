# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""The ECMWF endpoints the skills depend on must still exist (run before every release)."""

import json

import check_endpoints as ce
import pytest
from conftest import FIXTURES

STATUS = json.loads((FIXTURES / "ecmwf-status.json").read_text())
SESSIONS = json.loads((FIXTURES / "ecmwf-status-sessions.json").read_text())


def fetcher(overrides=None):
    def fetch(url):
        for key, value in (overrides or {}).items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        if url == ce.st.STATUS:
            return 200, json.dumps(STATUS)
        if url == ce.st.SESSIONS:
            return 200, json.dumps(SESSIONS)
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


@pytest.mark.live
def test_live_endpoints_exist():
    assert ce.check() == []


def test_transient_failures_are_retried():
    calls = {"n": 0}
    base = fetcher()

    def flaky(url):
        if url == "https://polytope.ecmwf.int/api/v1/test":
            calls["n"] += 1
            if calls["n"] == 1:
                raise TimeoutError("timed out")
        return base(url)

    assert ce.check(fetch=ce.retrying(flaky, delay=0)) == []
    assert calls["n"] == 2
