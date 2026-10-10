# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""ECMWF service status lookup used when a network request fails."""

import json

import pytest
from conftest import FIXTURES, SKILLS, load_script

st = load_script("ecmwf-open-data", "ecmwf_status")
STATUS = json.loads((FIXTURES / "ecmwf-status.json").read_text())
SESSIONS = json.loads((FIXTURES / "ecmwf-status-sessions.json").read_text())


def fake(routes):
    def fetch(url):
        for key, value in routes.items():
            if key in url:
                if isinstance(value, Exception):
                    raise value
                return value
        raise OSError(f"unexpected {url}")

    return fetch


QUIET = {"/status/sessions": {"nodes": []}, "/catalogue/v1/messages": {"messages": []}}


def routes(status=None, **extra):
    return fake({"/status/status": status or STATUS, **QUIET, **extra})


def degraded(title, note="Ongoing incident"):
    d = json.loads(json.dumps(STATUS))
    for n in d["nodes"]:
        if n["node"]["Title"] == title:
            n["node"]["Status"] = "Degraded"
            n["node"]["Last notification"] = note
    return d


def test_every_service_maps_to_a_status_source():
    for svc in (
        "open-data",
        "webapi",
        "polytope",
        "wms",
        "opencharts",
        "cds",
        "ads",
        "ecds",
        "destine",
    ):
        assert svc in st.SERVICES


def test_all_ok():
    r = st.outage_report(
        "wms", fetch=fake({"/status/status": STATUS, "/status/sessions": {"nodes": []}}), now=0
    )
    assert r["state"] == "ok" and "WEB-SERVICES" in r["summary"]


def test_degraded_component_reported():
    r = st.outage_report(
        "cds",
        fetch=fake(
            {
                "/status/status": degraded("Data Stores", "CDS &amp; ADS slow"),
                "/status/sessions": {"nodes": []},
                "/catalogue/v1/messages": {"messages": []},
            }
        ),
        now=0,
    )
    assert r["state"] == "degraded" and "CDS & ADS slow" in r["summary"]


def test_active_maintenance_session():
    sessions = {
        "nodes": [
            {
                "node": {
                    "Title": "MARS upgrade",
                    "Start Time": "1000",
                    "Duration": "60",
                    "Services": "MARS",
                    "Impact": "Potential Service Downtime",
                }
            }
        ]
    }
    r = st.outage_report(
        "webapi",
        fetch=fake({"/status/status": STATUS, "/status/sessions": sessions}),
        now=1000 + 30 * 60,
    )
    assert r["state"] == "maintenance" and "MARS upgrade" in r["summary"]
    later = st.outage_report(
        "webapi",
        fetch=fake({"/status/status": STATUS, "/status/sessions": sessions}),
        now=1000 + 7200,
    )
    assert later["state"] == "ok"


def test_datastore_live_message():
    msgs = {
        "messages": [
            {"summary": "Queue paused", "severity": "warning", "live": True},
            {"summary": "old", "severity": "critical", "live": False},
        ]
    }
    r = st.outage_report(
        "ads",
        fetch=fake(
            {
                "/status/status": STATUS,
                "/status/sessions": {"nodes": []},
                "/catalogue/v1/messages": msgs,
            }
        ),
        now=0,
    )
    assert r["state"] == "degraded" and "Queue paused" in r["summary"] and "old" not in r["summary"]


def test_status_unreachable_is_unknown():
    r = st.outage_report("open-data", fetch=fake({"/status/": OSError("down")}), now=0)
    assert r["state"] == "unknown" and "https://status.ecmwf.int" in r["summary"]


def test_network_barrier_has_steps_and_status():
    b = st.network_barrier(
        "polytope",
        "Connection refused",
        fetch=fake({"/status/status": STATUS, "/status/sessions": {"nodes": []}}),
        now=0,
    )
    text = "\n".join(b["user_steps"])
    assert "https://status.ecmwf.int" in text and b["blocked"].startswith("cannot reach")
    assert "Connection refused" in b["why"] and "ECMWF status" in b["why"]


def test_copies_are_identical():
    canonical = (SKILLS / "ecmwf-open-data" / "scripts" / "ecmwf_status.py").read_text()
    for skill in (
        "ecmwf-opencharts-wms",
        "ecmwf-cds-ads",
        "ecmwf-mars",
        "ecmwf-polytope",
        "ecmwf-destine",
        "ecmwf-observations",
    ):
        assert (SKILLS / skill / "scripts" / "ecmwf_status.py").read_text() == canonical, skill


@pytest.mark.live
def test_live_status_feed():
    r = st.outage_report("cds")
    assert r["state"] in ("ok", "degraded", "maintenance") and "Data Stores" in r["summary"]
