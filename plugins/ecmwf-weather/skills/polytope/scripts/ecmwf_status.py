#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""What ECMWF says about a service's availability — used when a network request fails.

Standard library only. Identical copies live in every skill that calls ECMWF services (skills
are self-contained); tests/status/test_ecmwf_status.py keeps them identical.

Sources (the same ones status.ecmwf.int and the data-store "Live status" pages use):
  https://apps.ecmwf.int/status/status     component status (Ok / Degraded / …)
  https://apps.ecmwf.int/status/sessions   planned maintenance windows
  <data store>/api/catalogue/v1/messages   CDS/ADS/ECDS banners (live, severity)

  python3 ecmwf_status.py cds        # report for one service
  python3 ecmwf_status.py            # all services
"""

from __future__ import annotations

import html
import json
import re
import sys
import time
import urllib.request

STATUS = "https://apps.ecmwf.int/status/status"
SESSIONS = "https://apps.ecmwf.int/status/sessions"
STATUS_PAGE = "https://status.ecmwf.int"
# Short: this runs on an error path, and the status service can be down with everything else.
TIMEOUT_S = 5
UA = "ecmwf-weather-skills-status/0.1"

# service -> (status.ecmwf.int components, session/notification tags, data-store host or None)
SERVICES = {
    "open-data": (["DISSEMINATION", "WEB-SERVICES"], ["DISSEMINATION"], None),
    "webapi": (["WebMARS", "MARS"], ["WebMARS", "MARS"], None),
    "polytope": (["WEB-SERVICES"], ["POLYTOPE"], None),
    "wms": (["WEB-SERVICES"], ["WEB-SERVICES"], None),
    "opencharts": (["WEB-SERVICES"], ["WEB-SERVICES"], None),
    "cds": (["Data Stores"], ["Data Stores"], "https://cds.climate.copernicus.eu"),
    "ads": (["Data Stores"], ["Data Stores"], "https://ads.atmosphere.copernicus.eu"),
    "ecds": (["Data Stores"], ["Data Stores"], "https://ecds.ecmwf.int"),
    # DestinE has no machine-readable status; maintenance is announced as platform news.
    "destine": ([], [], None),
}
# Client error texts that mean the service could not be reached at all.
NETWORK_ERROR = re.compile(
    r"(?i)connection (refused|reset|aborted)|max retries exceeded|name or service not known"
    r"|timed out|temporary failure in name resolution|network is unreachable"
)
DESTINE_NEWS = "https://platform.destine.eu/category/technical/"


def _get(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())  # served as text/plain


def _services(field) -> set[str]:
    if isinstance(field, dict):
        return {str(v) for v in field.values()}
    return {str(field)} if field else set()


def outage_report(service: str, fetch=None, now: float | None = None) -> dict:
    """{"state": ok|degraded|maintenance|unknown, "summary": text, "details": [...]}."""
    components, tags, host = SERVICES[service]
    fetch = fetch or _get
    now = time.time() if now is None else now
    details: list[str] = []
    state = "ok"
    try:
        nodes = {n["node"]["Title"]: n["node"] for n in fetch(STATUS)["nodes"]}
        for c in components:
            node = nodes.get(c)
            if node is None:
                continue
            s = str(node.get("Status", "")).strip().lower()
            if s not in ("ok", "0"):
                state = "degraded"
                note = html.unescape(node.get("Last notification", ""))
                details.append(f"{c}: {node.get('Status')} — {note}")
            else:
                details.append(f"{c}: Ok")
        for n in fetch(SESSIONS).get("nodes", []):
            s = n["node"]
            start, minutes = int(s.get("Start Time", 0)), int(s.get("Duration", 0) or 0)
            if _services(s.get("Services")) & set(tags) and start <= now < start + minutes * 60:
                if state == "ok":
                    state = "maintenance"
                details.append(
                    f"maintenance now: {html.unescape(s.get('Title', ''))} ({s.get('Impact', '')})"
                )
        if host:
            for m in fetch(f"{host}/api/catalogue/v1/messages").get("messages", []):
                if m.get("live") and m.get("severity") in ("critical", "warning"):
                    state = "degraded"
                    details.append(f"{host} notice ({m['severity']}): {m.get('summary', '')}")
    except Exception as e:  # the status service itself may be unreachable
        return {
            "service": service,
            "state": "unknown",
            "details": [],
            "summary": f"ECMWF status could not be retrieved ({type(e).__name__}); "
            f"check {STATUS_PAGE} manually.",
        }
    if service == "destine":
        details.append(f"no status feed; maintenance notices: {DESTINE_NEWS}")
    summary = {
        "ok": "ECMWF reports no problem",
        "degraded": "ECMWF reports a problem",
        "maintenance": "ECMWF reports maintenance in progress",
    }[state]
    return {
        "service": service,
        "state": state,
        "details": details,
        "summary": f"{summary} ({'; '.join(details) or 'no component listed'}). "
        f"Status page: {STATUS_PAGE}",
    }


def network_barrier(service: str, error: str, fetch=None, now: float | None = None) -> dict:
    """A BLOCKED report for a network failure, including what ECMWF says about the service."""
    r = outage_report(service, fetch, now)
    known = r["state"] in ("degraded", "maintenance")
    return {
        "blocked": f"cannot reach the ECMWF {service} service",
        "why": f"{error}. ECMWF status: {r['summary']}",
        "user_steps": [
            f"1. Check {STATUS_PAGE} for incidents and planned maintenance.",
            "2. "
            + (
                "ECMWF has reported the problem: wait for it to clear, then retry."
                if known
                else "ECMWF reports no problem: check this machine's network, proxy or "
                "firewall can reach the service, then retry."
            ),
            "3. If it persists, report it via https://support.ecmwf.int "
            "(DestinE: https://platform.destine.eu/contact/).",
        ],
        "agent": (
            "Tell the user ECMWF has a known outage/maintenance and stop retrying."
            if known
            else "Retry once (a mirror if the skill has one); then give these steps. Never "
            "substitute another provider's data."
        ),
        "status": r,
    }


def main(argv: list[str]) -> int:
    for svc in argv[1:] or list(SERVICES):
        print(json.dumps(outage_report(svc), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
