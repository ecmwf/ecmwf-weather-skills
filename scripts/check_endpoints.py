# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Check that every ECMWF endpoint the skills depend on still exists — not that it is healthy.

  python3 scripts/check_endpoints.py      # exit 1 and list what moved or disappeared

Run by `make check-endpoints`, which `make release-prepare` and the weekly CI call:

- the status feeds behind status.ecmwf.int still parse with the expected shape, and every
  component mapped in ecmwf_status.SERVICES is still listed (status Ok or not doesn't matter);
- each data store's /api/catalogue/v1/messages still answers with a message list;
- the ECMWF MCP router's /status still lists exactly the services ecmwf_mcp.BACKENDS calls
  directly when the router is down;
- every service host still answers HTTP (401/403 count as present: authentication is expected);
- the optional services the scripts call as APIs answer the request the scripts make: both MCP
  backends an `initialize` message, NOAA Aviation Weather (non-ECMWF; station metadata for
  `ecmwf-observations`) a station list. An outage warns; drift fails.

A network error or a rate-limited answer (HTTP 429) is asked again before it counts.
"""

from __future__ import annotations

import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/ecmwf-weather/skills/ecmwf-open-data/scripts"


def _load(name: str, directory: Path = SCRIPTS):
    spec = importlib.util.spec_from_file_location(name, directory / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # ecmwf_mcp and obs import their sibling ecmwf_status by name
    sys.modules.setdefault(name, module)
    spec.loader.exec_module(module)
    return module


st = _load("ecmwf_status")
mcp = _load("ecmwf_mcp")
obs = _load("obs", ROOT / "plugins/ecmwf-weather/skills/ecmwf-observations/scripts")

# One cheap URL per service that must answer (any HTTP status below 404, or 401/403/405).
PROBES = {
    "open-data": "https://data.ecmwf.int/forecasts/",
    "webapi": "https://api.ecmwf.int/v1/",
    "polytope": "https://polytope.ecmwf.int/api/v1/test",
    "wms": "https://eccharts.ecmwf.int/wms/?token=public&service=WMS&request=GetCapabilities"
    "&version=1.3.0",
    "opencharts": "https://charts.ecmwf.int/opencharts-api/v1/search/?package=opencharts",
    "cds": "https://cds.climate.copernicus.eu/api/catalogue/v1/collections?limit=1",
    "ads": "https://ads.atmosphere.copernicus.eu/api/catalogue/v1/collections?limit=1",
    "ecds": "https://ecds.ecmwf.int/api/catalogue/v1/collections?limit=1",
    "destine": "https://polytope.lumi.apps.dte.destination-earth.eu/api/v1/test",
    # The ERA5 collection the cds-ads skill reads (era5-point falls back to the CDS without it).
    "era5-edr": "https://polytope-edr.lumi.apps.dte.destination-earth.eu/collections/"
    "era5-single-levels?f=json",
    "destine-mn5": "https://polytope.mn5.apps.dte.destination-earth.eu/api/v1/test",
    "polytope-collections": "https://polytope.ecmwf.int/api/v1/collections",  # 401 = present
    "destine-lumi-collections": "https://polytope.lumi.apps.dte.destination-earth.eu/api/v1/collections",
    "destine-mn5-collections": "https://polytope.mn5.apps.dte.destination-earth.eu/api/v1/collections",
    "destine-leonardo-collections": (
        "https://polytope.leonardo.apps.dte.destination-earth.eu/api/v1/collections"
    ),
    "destine-news": st.DESTINE_NEWS,
    # ECMWF's DestinE data catalogue (Omnicat), a test deployment: when it moves, update
    # CATALOGUE_URL in ecmwf-destine/scripts/destine.py too.
    "destine-catalogue": "https://catalogue-test.lumi.apps.dte.destination-earth.eu/health",
    "mcp": st.SERVICE_STATUS["mcp"],
}
PRESENT = {401, 403, 405}
# Optional services: the skills work without them, so an outage (HTTP 5xx, timeout, refused
# connection) is a warning; drift — a host that no longer resolves, HTTP 404/410, a changed
# status document — still fails the release.
OPTIONAL = {"mcp"}

# Optional services the scripts call as APIs, each probed with the request the scripts make
# (a plain GET of them says nothing: the MCP endpoints answer 406, the NOAA API base 404).
# The same policy as OPTIONAL: an outage warns; drift fails.
MCP_PROBES = {f"mcp-{name}": url for name, url in sorted(mcp.BACKENDS.items())}
# A station every METAR network lists; the path, query and headers are those of
# obs.noaa_station_info, the one function that asks NOAA Aviation Weather for station metadata.
NOAA_STATION = "LPPT"
NOAA_PROBE = f"{obs.NOAA_API}/stationinfo?ids={NOAA_STATION}&format=json"
NOAA_HEADERS = {"User-Agent": obs.UA, "Accept": "application/json"}
# URLs the skills name as API bases or endpoints rather than as pages to read:
# `check_links.py` leaves them out because this script probes them.
SERVICE_ENDPOINTS = (*mcp.BACKENDS.values(), obs.NOAA_API)
TIMEOUT_S = 30


def probed_urls() -> list[str]:
    """Every URL this script requests, apart from the ones read from the status feeds."""
    return [*PROBES.values(), *MCP_PROBES.values(), NOAA_PROBE]


def _mcp_initialize_request() -> dict:
    """The first message of an MCP session, as ecmwf_mcp.Client sends it."""
    message = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {"protocolVersion": mcp.PROTOCOL, "capabilities": {}, "clientInfo": mcp.CLIENT},
    }
    return {
        "data": json.dumps(message).encode(),
        "headers": {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
    }


def _mcp_problem(body: str) -> str | None:
    """Why `body` is not an MCP initialise result (JSON or event stream), or None."""
    kind = "application/json" if body.lstrip().startswith(("{", "[")) else "text/event-stream"
    try:
        messages = mcp.parse_messages(kind, body.encode())
    except ValueError:
        return "answers HTTP 200, but not with an MCP initialize result"
    for m in messages:
        result = m.get("result") if isinstance(m, dict) else None
        if isinstance(result, dict) and m.get("id") == 1 and result.get("protocolVersion"):
            return None
    return "answers HTTP 200, but not with an MCP initialize result"


def _noaa_problem(body: str) -> str | None:
    """Why `body` is not the station list obs.noaa_station_info reads, or None."""
    try:
        stations = json.loads(body)
    except ValueError:
        return "answers HTTP 200, but not with JSON any more"
    if isinstance(stations, list) and any(
        isinstance(s, dict) and str(s.get("icaoId", "")).upper() == NOAA_STATION for s in stations
    ):
        return None
    return f"changed shape (expected a JSON list of stations with icaoId {NOAA_STATION})"


def _fetch(url: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, str]:
    """GET `url`, or POST `data` to it; returns the HTTP status and the body."""
    all_headers = {"User-Agent": "ecmwf-weather-skills-endpoints/0.1", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=all_headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, r.read(2_000_000).decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


# A release gate must not fail on one network hiccup: retry before reporting a problem.
RETRIES = 3
# HTTP 429 is a hiccup too: data.ecmwf.int throttles bursts of requests (as right after the
# live tests) and sends no Retry-After, so the same growing pause applies.
RETRY_STATUS = {429}


def retrying(fetch, retries: int = RETRIES, delay: float = 2.0):
    import time

    def get(url, **request):
        for attempt in range(retries):
            last = attempt == retries - 1
            try:
                code, body = fetch(url, **request)
            except Exception:
                if last:
                    raise
            else:
                if code not in RETRY_STATUS or last:
                    return code, body
            time.sleep(delay * (attempt + 1))
        raise RuntimeError("unreachable")

    return get


def _json(fetch, url: str, problems: list[str]):
    try:
        code, body = fetch(url)
    except Exception as e:
        problems.append(f"{url}: unreachable ({type(e).__name__}: {e})")
        return None
    if code != 200:
        problems.append(f"{url}: HTTP {code}")
        return None
    try:
        return json.loads(body)
    except ValueError:
        problems.append(f"{url}: not JSON any more")
        return None


def _unresolvable(e: Exception) -> bool:
    import socket

    return isinstance(getattr(e, "reason", None), socket.gaierror) or isinstance(e, socket.gaierror)


def check(fetch=None, warnings: list[str] | None = None) -> list[str]:
    """Problems that must block a release; outages of OPTIONAL services go to `warnings`."""
    fetch = fetch or retrying(_fetch)
    problems: list[str] = []
    warnings = [] if warnings is None else warnings

    status = _json(fetch, st.STATUS, problems)
    if status is not None:
        try:
            titles = {n["node"]["Title"] for n in status["nodes"]}
            for n in status["nodes"]:
                n["node"]["Status"]
        except (KeyError, TypeError):
            problems.append(
                f"{st.STATUS}: status feed changed shape (expected nodes[].node.Title/Status)"
            )
        else:
            wanted = {c for comps, _tags, _host in st.SERVICES.values() for c in comps}
            for c in sorted(wanted - titles):
                problems.append(
                    f"{st.STATUS}: component '{c}' is no longer listed — update "
                    "ecmwf_status.SERVICES"
                )

    sessions = _json(fetch, st.SESSIONS, problems)
    if sessions is not None and not isinstance(sessions.get("nodes"), list):
        problems.append(f"{st.SESSIONS}: maintenance feed changed shape (expected nodes[])")

    for _comps, _tags, host in st.SERVICES.values():
        if host:
            url = f"{host}/api/catalogue/v1/messages"
            msgs = _json(fetch, url, problems)
            if msgs is not None and not isinstance(msgs.get("messages"), list):
                problems.append(f"{url}: changed shape (expected messages[])")

    # Reachability is the "mcp" probe's job below; this checks what the router lists.
    router_url = st.SERVICE_STATUS["mcp"]
    try:
        code, body = fetch(router_url)
        router = json.loads(body) if code == 200 else None
    except ValueError:
        router = None
        problems.append(f"{router_url}: not JSON any more")
    except Exception:
        router = None
    if router is not None:
        listed = {s.get("name"): s.get("url") for s in router.get("services") or []}
        for name, url in sorted(mcp.BACKENDS.items()):
            if listed.get(name) != url:
                problems.append(
                    f"{st.SERVICE_STATUS['mcp']}: the router lists service '{name}' at "
                    f"{listed.get(name)!r}, ecmwf_mcp.BACKENDS has {url!r} — update "
                    "ecmwf_mcp.BACKENDS"
                )
        for name in sorted(set(listed) - set(mcp.BACKENDS)):
            problems.append(
                f"{st.SERVICE_STATUS['mcp']}: the router lists a service '{name}' that "
                "ecmwf_mcp.BACKENDS lacks — add it"
            )

    for name, url in PROBES.items():
        try:
            code, _ = fetch(url)
        except Exception as e:
            if name in OPTIONAL and not _unresolvable(e):
                warnings.append(f"{name}: {url} unreachable ({type(e).__name__}: {e}) — optional")
            elif name in OPTIONAL:
                problems.append(f"{name}: {url} no longer resolves ({e})")
            else:
                problems.append(f"{name}: {url} unreachable ({type(e).__name__}: {e})")
            continue
        if code >= 400 and code not in PRESENT:
            if name in OPTIONAL and code >= 500:
                warnings.append(f"{name}: {url} answers HTTP {code} — optional, not blocking")
            else:
                problems.append(f"{name}: {url} answers HTTP {code}")

    requests = {
        **{
            name: (url, _mcp_initialize_request(), _mcp_problem) for name, url in MCP_PROBES.items()
        },
        "noaa": (NOAA_PROBE, {"headers": NOAA_HEADERS}, _noaa_problem),
    }
    for name, (url, request, answer_problem) in requests.items():
        try:
            code, body = fetch(url, **request)
        except Exception as e:
            if _unresolvable(e):
                problems.append(f"{name}: {url} no longer resolves ({e})")
            else:
                warnings.append(f"{name}: {url} unreachable ({type(e).__name__}: {e}) — optional")
            continue
        if code >= 500:
            warnings.append(f"{name}: {url} answers HTTP {code} — optional, not blocking")
        elif code != 200:
            problems.append(f"{name}: {url} answers HTTP {code}")
        elif (why := answer_problem(body)) is not None:
            problems.append(f"{name}: {url} {why}")
    return sorted(set(problems))


def main() -> int:
    warnings: list[str] = []
    problems = check(warnings=warnings)
    for w in sorted(set(warnings)):
        print(f"WARNING: {w}")
    for p in problems:
        print(f"MISSING: {p}")
    print(
        "all endpoints present"
        if not problems
        else f"{len(problems)} endpoint problem(s) — "
        "update the skills (ecmwf_status.SERVICES, script URLs) before releasing"
    )
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
