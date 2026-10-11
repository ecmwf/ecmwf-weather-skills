#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.12"
# dependencies = ["polytope-client>=0.7"]
# ///
"""Destination Earth (DestinE) Digital Twin data via the DestinE Polytope service.

check, setup, request and options run on plain Python 3 (standard library only); retrieve
needs uv (installs polytope-client). `request` and `options` ask ECMWF's DestinE data catalogue
(public metadata, no token; DESTINE_CATALOGUE_URL overrides its address), the source of truth
for keys and values: `request` normalises values, maps parameters to their monthly-mean
equivalents, names any unavailable axis with its valid values, and returns the catalogue's
MARS request(s) with the Polytope server that holds the data. When the catalogue cannot be
reached, `request` only lints offline and says the request was NOT validated.

Retrieving needs UPGRADED access on the DestinE Platform — `setup` explains how to request it.
The token lives in ~/.polytopeapirc-destine (or DESTINE_POLYTOPE_KEY) so it never overwrites an
ECMWF Polytope key in ~/.polytopeapirc.

  python3 destine.py check                     # token and collections per bridge (never printed)
  python3 destine.py setup                     # how to request access and authenticate
  python3 destine.py request climate-dt --param 167 --start 2014-01-01 --end 2014-01-31
  python3 destine.py request climate-dt --frequency monthly --experiment ssp3-7.0 --year 2040
  python3 destine.py request extremes-dt --param 167 --lat 38.72 --lon -9.14
  python3 destine.py options climate-dt --axis param --frequency monthly --experiment ssp3-7.0
  uv run  destine.py retrieve req.json -o out.grib    # req.json from `request --json`
"""

from __future__ import annotations

import argparse
import datetime
import http.client
import importlib
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Never write __pycache__ into the installed skill (it may be read-only, and skills must
# not be modified); sibling scripts are imported below.
sys.dont_write_bytecode = True
ecmwf_status = importlib.import_module(
    "ecmwf_status"
)  # sibling script: what ECMWF says about service status

TOKEN_FILE = ".polytopeapirc-destine"
TOKEN_ENV = "DESTINE_POLYTOPE_KEY"
LUMI = "polytope.lumi.apps.dte.destination-earth.eu"
MN5 = "polytope.mn5.apps.dte.destination-earth.eu"
LEONARDO = "polytope.leonardo.apps.dte.destination-earth.eu"
COLLECTION = "destination-earth"
DATASETS = ("climate-dt", "extremes-dt", "on-demand-extremes-dt")
CLIMATE_DT_DOI = "10.21957/79c6af3105"
# The only hosts the token is ever sent to.
DESTINE_HOST = re.compile(r"polytope\.[a-z0-9-]+\.apps\.dte\.destination-earth\.eu")


# --- credentials ----------------------------------------------------------------------------------


def token_source(env=os.environ, home: Path | None = None) -> str | None:
    """Where the DestinE token comes from — never its value."""
    home = home or Path.home()
    if env.get(TOKEN_ENV):
        return TOKEN_ENV
    if (home / TOKEN_FILE).exists():
        return f"~/{TOKEN_FILE}"
    return None


class MalformedToken(ValueError):
    """The token file exists but holds no usable token. Carries key names, never values."""

    def __init__(self, problem: str, keys=()):
        self.problem = problem
        self.keys = sorted({str(k) for k in keys if re.fullmatch(r"[A-Za-z_]{1,24}", str(k))})
        super().__init__(f"~/{TOKEN_FILE} is malformed: {problem}")


def read_token(env=os.environ, home: Path | None = None) -> str | None:
    """The token (DESTINE_POLYTOPE_KEY, else user_key in ~/.polytopeapirc-destine); None when
    neither exists; MalformedToken when the file cannot be used."""
    home = home or Path.home()
    if env.get(TOKEN_ENV):
        return env[TOKEN_ENV]
    f = home / TOKEN_FILE
    if not f.exists():
        return None
    try:
        text = f.read_bytes().decode("utf-8")
    except OSError as e:
        raise MalformedToken(f"cannot be read ({e.strerror or type(e).__name__})") from None
    except UnicodeDecodeError:
        raise MalformedToken("is not UTF-8 text") from None
    if not text.strip():
        raise MalformedToken("is empty")
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        names = re.findall(r"(?m)^\s*([A-Za-z_]{1,24})\s*[:=]", text)
        raise MalformedToken("is not JSON", names) from None
    if not isinstance(d, dict):
        raise MalformedToken(f"holds a JSON {type(d).__name__}, not an object")
    key = d.get("user_key")
    if not (isinstance(key, str) and key.strip()):
        raise MalformedToken("has no user_key", d.keys())
    return key


def malformed_barrier(e: MalformedToken) -> dict:
    return {
        "blocked": f"~/{TOKEN_FILE} is malformed",
        "why": f"it {e.problem}; keys found: {', '.join(e.keys) or 'none readable'}. It must "
        'be JSON with the token under "user_key", as desp-authentication.py writes it.',
        "user_steps": [
            "1. Write a fresh token file (your password is prompted, never put it on the "
            "command line):\n   python polytope-examples/desp-authentication.py -u <DestinE "
            f"username> -o ~/{TOKEN_FILE}\n   chmod 600 ~/{TOKEN_FILE}",
            "   (polytope-examples: https://github.com/destination-earth-digital-twins/"
            "polytope-examples; python3 destine.py setup has every step.)",
            "2. Re-run: python3 destine.py check",
        ],
        "agent": "Relay these steps; never open or print the file and never ask for the token "
        "or password in chat.",
    }


def setup_steps() -> list[str]:
    return [
        "1. Register (free) on the DestinE Platform: https://platform.destine.eu — a basic account "
        "does NOT include Digital Twin data.",
        "2. Complete your user profile: https://auth.destine.eu/realms/desp/account/",
        "3. Request upgraded access: https://platform.destine.eu/access-policy-upgrade/ — 'Apply "
        "here', fill in the form, accept the T&Cs for DestinE Priority Users. The European "
        "Commission reviews it (several business days); the answer comes by email. Eligible: EU "
        "institutions and public authorities of EU / Digital Europe Programme countries, public "
        "research and academia, EU-based companies, ESA/ECMWF/EUMETSAT staff; others case by case.",
        "4. Once approved, get a token (your password is prompted, never put it on the command "
        "line):\n   git clone https://github.com/destination-earth-digital-twins/polytope-examples"
        "\n   pip install polytope-client conflator lxml"
        "\n   python polytope-examples/desp-authentication.py -u <DestinE username> "
        "-o ~/.polytopeapirc-destine\n   chmod 600 ~/.polytopeapirc-destine",
        "   The separate file keeps an ECMWF Polytope key in ~/.polytopeapirc intact. On HTTP 401 "
        "later, run the script again.",
        "5. Help: https://platform.destine.eu/contact/ (FAQ: https://platform.destine.eu/support-faq/)",
    ]


def blocked(b: dict, as_json: bool = False, code: int = 4) -> int:
    """Report an access barrier: what blocks, why, what the user must do, what the agent does.

    Every legal or technical barrier gets this report (AGENTS.md "When access is blocked")."""
    lines = [f"BLOCKED: {b['blocked']}", f"Why: {b['why']}", "What the user needs to do:"]
    lines += [f"  {s}" for s in b["user_steps"]]
    lines.append(f"For the agent: {b['agent']}")
    print("\n".join(lines), file=sys.stderr)
    if as_json:
        print(json.dumps({"blocked": b}, indent=2))
    return code


def barrier_for_error(message: str) -> dict | None:
    if ecmwf_status.NETWORK_ERROR.search(message):
        return ecmwf_status.network_barrier("destine", message.splitlines()[-1][:200])
    if re.search(r"(?i)\b401\b|unauthori[sz]ed|token", message):
        return {
            "blocked": "DestinE rejected the token (missing or expired)",
            "why": "DestinE tokens are long-lived but expire after inactivity or a password "
            "change.",
            "user_steps": [setup_steps()[3], "Then tell the agent to retry."],
            "agent": "Give the re-authentication step; never ask for the password in chat.",
        }
    if re.search(r"(?i)\b403\b|forbidden|not allowed|access denied", message):
        return {
            "blocked": "this DestinE account lacks upgraded access to Digital Twin data",
            "why": "Digital Twin data requires upgraded access granted by the European Commission.",
            "user_steps": [*setup_steps()[:3], setup_steps()[5]],
            "agent": "Give these steps; offer open-data or cds-ads alternatives meanwhile.",
        }
    return None


# --- collections (authenticated) ---------------------------------------------------------------

# The collections list is a small JSON document; 30 s tolerates a slow data bridge.
COLLECTIONS_TIMEOUT_S = 30


def _get_json(url: str, headers: dict) -> tuple[int, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "ecmwf-weather-skills", **headers})
    try:
        with urllib.request.urlopen(req, timeout=COLLECTIONS_TIMEOUT_S) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def collections_on(address: str, token: str, fetch=_get_json) -> list[str]:
    """GET /api/v1/collections on one data bridge; raises on 401/403 or other failures."""
    status, body = fetch(
        f"https://{address}/api/v1/collections", {"Authorization": f"Bearer {token}"}
    )
    if status != 200:
        raise RuntimeError(f"HTTP {status}")
    return list(body.get("message", []))


def collections_by_bridge(token: str, fetch=_get_json) -> dict:
    """Collections the token may use on each bridge, or the error text for that bridge."""
    out: dict = {}
    for address in (LUMI, MN5, LEONARDO):
        try:
            out[address] = collections_on(address, token, fetch)
        except Exception as e:
            out[address] = f"error: {e}"
    return out


def preflight(req: dict, token: str, fetch=_get_json, address: str | None = None) -> dict | None:
    """Before retrieving: the target bridge must list the collection for this token."""
    address = address or address_for(req)
    try:
        cols = collections_on(address, token, fetch)
    except Exception as e:
        return barrier_for_error(f"{e} from {address}") or ecmwf_status.network_barrier(
            "destine", str(e)
        )
    if COLLECTION not in cols:
        return {
            "blocked": f"the '{COLLECTION}' collection is not available to this account on "
            f"{address}",
            "why": f"the token can use: {', '.join(cols) or 'no collections'} — Digital Twin data "
            "needs upgraded access granted by the European Commission.",
            "user_steps": [*setup_steps()[:3], setup_steps()[5]],
            "agent": "Give these steps; offer ecmwf-open-data or ecmwf-cds-ads alternatives "
            "meanwhile.",
        }
    return None


# --- the DestinE data catalogue (public metadata, no token) -----------------------------------

# ECMWF's catalogue of what the DestinE data bridges hold (Omnicat, built on ecmwf/qubed; its
# index is rebuilt from the bridges every few minutes). Only its read-only endpoints are used.
CATALOGUE_URL = "https://catalogue-test.lumi.apps.dte.destination-earth.eu"
CATALOGUE_ENV = "DESTINE_CATALOGUE_URL"
# Answers take well under a second; the configuration (labels of every MARS value) is ~0.8 MB.
CATALOGUE_TIMEOUT_S = 30
# Web servers reject long GET query strings; longer lists (dates) are left out of axis
# queries, whose answers then cover every value of those axes.
MAX_QUERY_CHARS = 4000
CATALOGUE_UA = "ecmwf-weather-skills-destine"
# Request keys that shape Polytope's answer but are not catalogue axes.
POSTPROCESSING = ("feature", "format", "grid", "area")
STREAMS = {"hourly": "clte", "monthly": "clmn"}
# Axes whose values are listed as runs (20140101-20141231) rather than one by one.
RANGE_AXES = ("date", "year")
# Axes without labels to normalise against (numbers, dates) or resolved separately (param).
UNLABELLED = ("param", "date", "time", "year", "step", "levelist")
MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")


class CatalogueUnavailable(Exception):
    """The catalogue could not be reached, or answered something unusable."""


def catalogue_url(env=os.environ) -> str:
    return (env.get(CATALOGUE_ENV) or CATALOGUE_URL).rstrip("/")


def _catalogue_fetch(url: str, body: dict | None = None):
    """GET, or POST a JSON body, and decode the JSON answer; every failure is
    CatalogueUnavailable with a one-line reason."""
    headers = {"User-Agent": CATALOGUE_UA, "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=CATALOGUE_TIMEOUT_S) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise CatalogueUnavailable(f"HTTP {e.code} from {url.split('?')[0]}") from None
    except urllib.error.URLError as e:
        raise CatalogueUnavailable(f"{type(e).__name__}: {e.reason}") from None
    except (OSError, ValueError, http.client.HTTPException) as e:  # timeout, reset, not JSON
        raise CatalogueUnavailable(f"{type(e).__name__}: {e}") from None


def _text(axis: str, v) -> str:
    if axis == "time" and isinstance(v, int):
        return f"{v:04d}"
    return str(v)


def _order_key(v: str):
    return (0, int(v), "") if v.isdigit() else (1, 0, v)


def _key(v) -> object:
    """Values compare as numbers when they are numbers (07 = 7, 0000 = 0), else case-blind."""
    s = str(v)
    return int(s) if s.isdigit() else s.lower()


def _axis_values(axis: str, v) -> list[str]:
    """An axis's values as strings. The catalogue encodes an axis stored with mixed types as a
    JSON string {"ints": [...], "strings": [...]} (Climate DT hours 0000-0900 are strings,
    1000-2300 integers)."""
    if isinstance(v, str):
        try:
            d = json.loads(v)
        except ValueError:
            return [v]
        if isinstance(d, dict):
            both = [*d.get("ints", []), *d.get("strings", [])]
            return sorted({_text(axis, x) for x in both}, key=_order_key)
        return [v]
    if isinstance(v, list):
        return [_text(axis, x) for x in v]
    return [_text(axis, v)]


def _query(selection: dict) -> str:
    """The URL query for a selection; while it is too long, the longest list is left out."""
    sel = dict(selection)
    while True:
        q = urllib.parse.urlencode({k: "/".join(v) for k, v in sel.items()}, safe="/")
        if len(q) <= MAX_QUERY_CHARS or not sel:
            return q
        del sel[max(sel, key=lambda k: len(sel[k]))]


class Catalogue:
    """The read-only catalogue endpoints `request` and `options` use. Selections map each MARS
    key to a list of values (strings)."""

    def __init__(self, base: str | None = None, fetch=None):
        self.base = (base or catalogue_url()).rstrip("/")
        self._fetch = fetch or _catalogue_fetch
        self._config: dict | None = None
        self._labels: dict = {}

    def config(self) -> dict:
        """Axis order per dataset, and labels for every MARS value (its "language")."""
        if self._config is None:
            c = self._fetch(f"{self.base}/ai-catalogue/config")
            if not (isinstance(c, dict) and isinstance(c.get("language"), dict)):
                raise CatalogueUnavailable("unexpected answer from /ai-catalogue/config")
            self._config = c
        return self._config

    def order(self, dataset: str) -> list[str]:
        p = self.config().get("presentation", {}).get("datasets", {}).get(dataset, {})
        return [str(a) for a in p.get("order", [])]

    def labels(self, axis: str) -> dict:
        """value -> {"names": short names, "label": label} for one axis."""
        if axis not in self._labels:
            values = (self.config()["language"].get(axis) or {}).get("values") or {}
            table = {}
            for v, e in values.items():
                e = e if isinstance(e, dict) else {}
                names = [str(n) for n in e.get("alternative_names") or []]
                table[str(v)] = {"names": names, "label": str(e.get("name") or "")}
            self._labels[axis] = table
        return self._labels[axis]

    def axes(self, selection: dict) -> dict:
        """Every axis's values under a selection; {} when nothing matches. Keys that are not
        axes of the matched data are ignored by the catalogue."""
        q = _query(selection)
        d = self._fetch(f"{self.base}/api/v2/axes/" + (f"?{q}" if q else ""))
        if not isinstance(d, dict):
            raise CatalogueUnavailable("unexpected answer from /api/v2/axes/")
        return {str(k): _axis_values(str(k), v) for k, v in d.items()}

    def locations(self, selection: dict) -> list[dict]:
        """The minimal MARS requests for a selection, per data location:
        [{"name", "url", "requests"}]; [] when nothing matches."""
        d = self._fetch(f"{self.base}/catalogue/locations", selection)
        locs = d.get("locations") if isinstance(d, dict) else None
        if not isinstance(locs, list):
            raise CatalogueUnavailable("unexpected answer from /catalogue/locations")
        return [x for x in locs if isinstance(x, dict) and x.get("requests")]

    def count(self, selection: dict) -> int:
        """How many MARS requests a (possibly partial) selection matches; 0: no data."""
        d = self._fetch(f"{self.base}/catalogue/mars-requests", selection)
        if not isinstance(d, dict):
            raise CatalogueUnavailable("unexpected answer from /catalogue/mars-requests")
        return len(d.get("requests") or [])


def address_from_url(url) -> str | None:
    """The Polytope server in a location URL from the catalogue — only a DestinE host over
    https; anything else is not trusted with the token."""
    parts = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    if parts and parts.scheme == "https" and DESTINE_HOST.fullmatch(parts.hostname or ""):
        return parts.hostname
    return None


def trusted_address(address) -> bool:
    return isinstance(address, str) and bool(DESTINE_HOST.fullmatch(address))


# --- requests -------------------------------------------------------------------------------------


def address_for(req: dict) -> str:
    """Which data bridge holds the data (Climate DT user guide, 'Data Structure and Keys') —
    the routing used when the catalogue cannot be asked."""
    ds = req.get("dataset")
    if ds != "climate-dt":
        return LUMI  # Extremes DT, On-Demand Extremes DT, nextGEMS
    if str(req.get("generation", "2")) == "1":
        return LEONARDO if req.get("model") == "ifs-fesom" else LUMI
    if req.get("activity") == "story-nudging" or req.get("model") == "ifs-nemo":
        return MN5
    return LUMI  # Gen2 ICON and IFS-FESOM


def _feature(lat, lon, axis: str) -> dict | None:
    if lat is None or lon is None:
        return None
    return {
        "type": "timeseries",
        "points": [[lat, lon]],
        "axes": ["latitude", "longitude"],
        "time_axis": axis,
    }


def default_activity(experiment: str) -> str:
    """The Climate DT activity an experiment belongs to; the catalogue confirms it."""
    e = experiment.lower()
    if e.startswith("ssp"):
        return "projections"
    if e.startswith("tplus"):
        return "story-nudging"
    return "baseline"


def _month(token: str) -> str:
    t = token.strip().lower()
    if t[:3] in MONTHS and not t.isdigit():
        return str(MONTHS.index(t[:3]) + 1)
    if t.isdigit() and 1 <= int(t) <= 12:
        return str(int(t))
    raise ValueError(f"month {token!r} is not 1-12 or a month name")


def _day(text: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise ValueError(f"--start/--end must be YYYY-MM-DD, not {text!r}") from None


def _days(start: str, end: str) -> str:
    """MARS dates for --start/--end: one day, or a to-range."""
    a, b = _day(start).strftime("%Y%m%d"), _day(end).strftime("%Y%m%d")
    return a if a == b else f"{a}/to/{b}"


def _year_month(start, end, year, month) -> dict:
    """month and year of monthly means: given, or derived from --start/--end."""
    if year is None and month is None and (start or end):
        a, b = _day(start or end), _day(end or start)
        if a > b:
            raise ValueError("--start is after --end")
        if a.year == b.year:
            years, months = [a.year], list(range(a.month, b.month + 1))
        elif a.month == 1 and b.month == 12:
            years, months = list(range(a.year, b.year + 1)), list(range(1, 13))
        else:
            raise ValueError(
                "monthly means select year x month, so a range across a year boundary would "
                "also select other months: use --year and --month, one request per year (e.g. "
                f"--year {a.year} --month {a.month}/to/12, then --year {b.year} --month "
                f"1/to/{b.month})"
            )
        return {"month": "/".join(map(str, months)), "year": "/".join(map(str, years))}
    months = [_month(m) for m in _expand("month", month or "1/to/12", names=True)]
    return {"month": "/".join(months), "year": "/".join(_expand("year", year or "2014"))}


def template(
    dataset: str,
    param: str = "167",
    start: str | None = None,
    end: str | None = None,
    model: str = "ifs-fesom",
    experiment: str = "hist",
    activity: str | None = None,
    lat: float | None = None,
    lon: float | None = None,
    frequency: str = "hourly",
    resolution: str = "standard",
    year: str | None = None,
    month: str | None = None,
) -> dict:
    """The request as built from the options, before the catalogue checks it."""
    if dataset == "climate-dt":
        r = {
            "class": "d1",
            "dataset": "climate-dt",
            "activity": activity or default_activity(experiment),
            "experiment": experiment,
            "generation": "2",
            "model": model,
            "realization": "1",
            "expver": "0001",
            "stream": STREAMS.get(frequency, frequency),
        }
        if frequency != "monthly":
            if not (start or end):
                start, end = "2014-01-01", "2014-01-31"
            r["date"] = _days(start or end, end or start)
            r["time"] = "0000/to/2300/by/0100"
        r.update({"resolution": resolution, "type": "fc", "levtype": "sfc", "param": param})
        if frequency == "monthly":
            r.update(_year_month(start, end, year, month))
        f = _feature(lat, lon, "date")
    elif dataset == "extremes-dt":
        if frequency == "monthly" or year or month:
            raise ValueError("the Extremes DT has no monthly means; give --start/--end dates")
        # without dates: yesterday's run (-1), which the catalogue replaces with its latest date
        date = _days(start or end, end or start) if start or end else "-1"
        r = {
            "class": "d1",
            "dataset": "extremes-dt",
            "expver": "0001",
            "stream": "oper",
            "type": "fc",
            "date": date,
            "time": "0000",
            "levtype": "sfc",
            "param": param,
        }
        f = _feature(lat, lon, "step")
        if f:
            f["range"] = {"start": 0, "end": 96}
        else:
            r["step"] = "0"
    else:
        raise ValueError(f"no template for {dataset!r}; choose climate-dt or extremes-dt")
    if f:
        r["feature"] = f
    return r


def lint(req: dict) -> list[str]:
    errs = []
    if req.get("class") != "d1":
        errs.append("DestinE Digital Twin data is class=d1")
    if req.get("dataset") not in DATASETS:
        errs.append(f"dataset must be one of {', '.join(DATASETS)}")
    if "format" in req:
        errs.append("remove 'format' — Polytope rejects it; feature requests return CoverageJSON")
    if req.get("dataset") == "climate-dt":
        for k in (
            "activity",
            "experiment",
            "generation",
            "model",
            "realization",
            "resolution",
            "stream",
        ):
            if k not in req:
                errs.append(f"climate-dt needs '{k}' (see references/requests.md)")
        if str(req.get("resolution", "standard")).lower() not in ("standard", "high"):
            errs.append("resolution is standard (longer availability) or high")
        stream = req.get("stream")
        if stream == "clmn":
            errs += [
                f"monthly means (clmn) have year and month, not '{k}'"
                for k in ("date", "time")
                if k in req
            ]
            errs += [f"monthly means (clmn) need '{k}'" for k in ("year", "month") if k not in req]
            if "feature" in req:
                errs.append(
                    "--lat/--lon point series need hourly data (time_axis date): for monthly "
                    "means request the field and extract the point with the ecmwf-earthkit skill"
                )
        elif stream == "clte":
            errs += [
                f"stream clte has date and time, not '{k}' (monthly means: clmn)"
                for k in ("year", "month")
                if k in req
            ]
            errs += [f"stream clte needs '{k}'" for k in ("date", "time") if k not in req]
    return errs


# The attribution the terms for DestinE users granted upgraded access (v2.1, section 3.4)
# prescribe, verbatim; bracketed parts are filled in by the user.
ATTRIBUTION = (
    "This service/application/tool/data is created [based on data of the European Union] "
    "and/or [using Destination Earth Platform] (but has been modified by [User's name])."
)


def attribution(dataset: str) -> dict:
    a = {
        "text": ATTRIBUTION,
        "how": "Keep the bracketed parts that apply (without brackets) and add '(but has been "
        "modified by <your name>)' whenever the data were changed — aggregation, "
        "transformation, interpolation or integration with other data are changes. Show it "
        "prominently with the product; never present derived products as original DestinE "
        "data.",
        "note": "Upgraded access is personal and strictly for official use within your "
        "activities (terms 2.1, 2.2). DestinE Data may not be reproduced, distributed or "
        "forwarded to third parties without the European Commission's prior written consent "
        "(2.5); only products from which the original data cannot be retrieved or reverse "
        "engineered may be created and distributed (2.3).",
        "citation": "",
    }
    if dataset == "climate-dt":
        a["citation"] = (
            "Destination Earth Digital Twin for Climate Change Adaptation (DestinE "
            f"Climate DT Generation 2), ECMWF, https://doi.org/{CLIMATE_DT_DOI}"
        )
    return a


# --- validation against the catalogue --------------------------------------------------------


def _range(key: str, start: str, end: str, by: str | None) -> list[str]:
    if key in ("date", "hdate"):
        a = datetime.datetime.strptime(start, "%Y%m%d")
        b = datetime.datetime.strptime(end, "%Y%m%d")
        days = range(0, (b - a).days + 1, int(by or 1))
        return [(a + datetime.timedelta(days=d)).strftime("%Y%m%d") for d in days]
    if key == "time":
        if by is None:
            raise ValueError(f"state the step of a time range, e.g. {start}/to/{end}/by/0100")

        def minutes(t: str) -> int:
            return int(t) // 100 * 60 + int(t) % 100 if len(t) > 2 else int(t) * 60

        return [
            f"{m // 60:02d}{m % 60:02d}"
            for m in range(minutes(start), minutes(end) + 1, minutes(by))
        ]
    return [str(v) for v in range(int(start), int(end) + 1, int(by or 1))]


def _expand(key: str, value, names: bool = False) -> list[str]:
    """MARS values as a list: "a/b", "a/to/b" and "a/to/b/by/c" (month names when names)."""
    tokens = [str(t).strip() for t in (value if isinstance(value, list) else str(value).split("/"))]
    if names:
        tokens = [_month(t) if t.lower() not in ("to", "by") else t for t in tokens]
    out, i = [], 0
    while i < len(tokens):
        if i + 2 < len(tokens) and tokens[i + 1].lower() == "to":
            has_by = i + 4 < len(tokens) and tokens[i + 3].lower() == "by"
            out += _range(key, tokens[i], tokens[i + 2], tokens[i + 4] if has_by else None)
            i += 5 if has_by else 3
        else:
            out.append(tokens[i])
            i += 1
    return out


def selection(req: dict) -> dict:
    """The catalogue selection for a request: MARS keys only, each value listed (the catalogue
    matches values one by one and does not read to/by ranges)."""
    return {k: _expand(k, v) for k, v in req.items() if k not in POSTPROCESSING}


def axis_order(cat: Catalogue, dataset: str, keys) -> list[str]:
    """The catalogue's axis order for the dataset, then any other keys alphabetically."""
    order = cat.order(dataset)
    return order + sorted(k for k in keys if k not in order)


def _canonical(table: dict, value: str) -> str:
    """The catalogue's spelling of a value — matched exactly, case-blind, as a number (07 is 7),
    by a short name (climate-monthly is clmn) or by its label (July is 7); unchanged when no
    single value matches."""
    if value in table:
        return value
    low = value.lower()
    for match in (
        lambda v, e: v.lower() == low,
        lambda v, e: value.isdigit() and v.isdigit() and int(v) == int(value),
        lambda v, e: low in (n.lower() for n in e["names"]),
        lambda v, e: e["label"].lower() == low,
    ):
        hits = [v for v, e in table.items() if match(v, e)]
        if len(hits) == 1:
            return hits[0]
    return value


def _normalise(sel: dict, cat: Catalogue, notes: list) -> None:
    for k, values in sel.items():
        if k in UNLABELLED:
            continue
        table = cat.labels(k)
        for i, v in enumerate(values):
            c = _canonical(table, v) if table else v
            if c != v:
                notes.append(f"{k} {v} -> {c} (the catalogue's value)")
                values[i] = c


def _is_relative(date: str) -> bool:
    return bool(re.fullmatch(r"-?\d{1,4}", date)) and int(date) <= 0


def _resolve_params(
    sel: dict, cat: Catalogue, notes: list, order: list[str], monthly: bool
) -> None:
    """Short names and labels become paramIds; for monthly means, an instantaneous parameter
    becomes its time-mean equivalent ("Time-mean <label>") when only that exists. What exists
    is judged by the axes before param in the catalogue's order, as validation does."""
    if "param" not in sel:
        return
    table = cat.labels("param")
    before = order[: order.index("param")] if "param" in order else list(sel)
    prefix = {k: sel[k] for k in before if k in sel and k != "param"}
    prefix = {k: v for k, v in prefix.items() if not (k == "date" and any(map(_is_relative, v)))}
    available = cat.axes(prefix).get("param", [])

    def prefer(hits: list) -> str:
        return next((h for h in hits if h in available), hits[0])

    def describe(p: str) -> str:
        e = table.get(p, {"names": [], "label": ""})
        return " ".join([p, *e["names"][:1]]) + (f" ({e['label']})" if e["label"] else "")

    for i, token in enumerate(sel["param"]):
        p = token
        if p not in table and not p.isdigit():
            low = p.lower()
            hits = [v for v, e in table.items() if low in (n.lower() for n in e["names"])]
            hits = hits or [v for v, e in table.items() if e["label"].lower() == low]
            if hits:
                p = prefer(sorted(hits, key=_order_key))
                notes.append(f"param {token} -> {p} ({table[p]['label']})")
        if monthly and available and p not in available and table.get(p, {}).get("label"):
            label = table[p]["label"].lower()
            means = [
                v
                for v in available
                if table.get(v, {}).get("label", "").lower()
                in (f"time-mean {label}", f"time mean {label}")
            ]
            if means:
                notes.append(
                    f"param {p} ({table[p]['label']}) -> {describe(means[0])}: monthly means "
                    "(stream clmn) hold time-mean parameters"
                )
                p = means[0]
        sel["param"][i] = p


def _resolve_relative_dates(sel: dict, cat: Catalogue, notes: list) -> None:
    """A relative date (-1: yesterday's Extremes DT run) becomes the latest date the catalogue
    lists for the selection."""
    if not any(map(_is_relative, sel.get("date", []))):
        return
    dates = cat.axes({k: v for k, v in sel.items() if k != "date"}).get("date", [])
    if dates:
        latest = max(dates, key=_order_key)
        notes.append(
            f"date {'/'.join(sel['date'])} -> {latest}: the latest date the catalogue lists for "
            "this selection (--start/--end choose others)"
        )
        sel["date"] = [latest]


def ranges(values: list[str]) -> list[str]:
    """Consecutive days (YYYYMMDD) or integers as first-last runs."""

    def following(v: str) -> str | None:
        if len(v) == 8 and v.isdigit():
            try:
                day = datetime.datetime.strptime(v, "%Y%m%d") + datetime.timedelta(days=1)
                return day.strftime("%Y%m%d")
            except ValueError:
                return None
        return str(int(v) + 1) if v.isdigit() else None

    runs: list[list[str]] = []
    for v in sorted(set(values), key=_order_key):
        if runs and following(runs[-1][-1]) == v:
            runs[-1].append(v)
        else:
            runs.append([v])
    return [r[0] if len(r) == 1 else f"{r[0]}-{r[-1]}" for r in runs]


def _option(cat: Catalogue, axis: str, value: str) -> dict:
    e = cat.labels(axis).get(value, {"names": [], "label": ""})
    return {"value": value, "name": e["names"][0] if e["names"] else "", "label": e["label"]}


def _describe(cat: Catalogue, axis: str, values: list[str]) -> str:
    if axis in RANGE_AXES:
        return ", ".join(ranges(values))
    out = []
    for v in values:
        label = cat.labels(axis).get(v, {}).get("label")
        out.append(f"{v} ({label})" if label else v)
    return "/".join(out)


def _error(message: str, axis: str | None = None) -> dict:
    return {"axis": axis, "values": [], "message": message}


def _unavailable(cat: Catalogue, axis: str, values: list[str], prefix: dict) -> dict:
    """The error for values of an axis the catalogue does not list under the selection of the
    axes before it, with the values it does list."""
    where = ", ".join(f"{k}={'/'.join(v)}" for k, v in prefix.items() if k != "class")
    options = cat.axes(prefix).get(axis) or []
    if not options:
        return _error(f"{axis} is not an axis of this data ({where}); leave it out", axis)
    listed = {_key(o) for o in options}
    missing = [v for v in values if _key(v) not in listed] or values
    e = {
        "axis": axis,
        "values": missing,
        "message": f"{axis} {_describe(cat, axis, missing)} is not available for {where}",
        "count": len(options),
    }
    if axis in RANGE_AXES:
        e["ranges"] = ranges(options)
    else:
        e["options"] = [_option(cat, axis, o) for o in options]
    return e


def _diagnose(cat: Catalogue, sel: dict, order: list[str]) -> dict:
    """Name the first axis, in the catalogue's order, whose values match nothing given the
    axes before it (matching only narrows as axes are added, so a bisection finds it)."""
    keys = [k for k in order if k in sel]
    if cat.count(sel):
        return _error(
            "the catalogue lists this data but no Polytope location serves it; ask DestinE "
            "support (https://platform.destine.eu/contact/)"
        )
    lo, hi = 0, len(keys)  # the first lo axes match something; all of them match nothing
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if cat.count({k: sel[k] for k in keys[:mid]}):
            lo = mid
        else:
            hi = mid
    axis = keys[hi - 1]
    return _unavailable(cat, axis, sel[axis], {k: sel[k] for k in keys[: hi - 1]})


def _covered(entries: list, axis: str) -> set | None:
    """The values of an axis the catalogue's requests cover; None when no request has it."""
    if not any(axis in r for _, r in entries):
        return None
    return {_key(v) for _, r in entries if axis in r for v in str(r[axis]).split("/")}


def _coverage(cat: Catalogue, sel: dict, order: list[str], entries: list) -> tuple:
    """Requested values missing from the catalogue's answer. Each axis's missing values are
    asked again on their own — the catalogue matches an axis stored with mixed types (Climate
    DT hours) against one type at a time — and reported when still missing."""
    errors, warnings = [], []
    keys = [k for k in order if k in sel]
    for n, axis in enumerate(keys):
        got = _covered(entries, axis)
        if got is None:
            warnings.append(f"{axis} is not an axis of this data; the catalogue left it out")
            continue
        missing = [v for v in sel[axis] if _key(v) not in got]
        if missing and len(missing) < len(sel[axis]):
            for loc in cat.locations({**sel, axis: missing}):
                entries = [*entries, *((loc, r) for r in loc["requests"])]
            got = _covered(entries, axis) or set()
            missing = [v for v in missing if _key(v) not in got]
        if missing:
            errors.append(_unavailable(cat, axis, missing, {k: sel[k] for k in keys[:n]}))
    return entries, errors, warnings


def _values(values: list[str]) -> str:
    seen = list(dict.fromkeys(values))
    if all(v.isdigit() for v in seen):
        seen.sort(key=int)
    return "/".join(seen)


def _merge(reqs: list[dict]) -> list[dict]:
    """Join requests that differ in one key only (re-asked values come back separately)."""
    out = [dict(r) for r in reqs]
    joined = True
    while joined:
        joined = False
        for i in range(len(out)):
            for j in range(i + 1, len(out)):
                a, b = out[i], out[j]
                diff = [k for k in a if a.get(k) != b.get(k)]
                if a.keys() == b.keys() and len(diff) <= 1:
                    for k in diff:
                        a[k] = _values(str(a[k]).split("/") + str(b[k]).split("/"))
                    del out[j]
                    joined = True
                    break
            if joined:
                break
    return out


def _ordered(req: dict, order: list[str]) -> dict:
    return {
        **{k: req[k] for k in order if k in req},
        **{k: v for k, v in req.items() if k not in order},
    }


def _routed(entries: list, order: list[str], req: dict, warnings: list) -> list[dict]:
    """The catalogue's requests per location, merged, with the Polytope server for each."""
    extras = {k: v for k, v in req.items() if k in POSTPROCESSING}
    groups: dict = {}
    for loc, r in entries:
        groups.setdefault((str(loc.get("name") or ""), loc.get("url")), []).append(r)
    out: list[dict] = []
    seen: list[dict] = []
    for (name, url), reqs in groups.items():
        address = address_from_url(url)
        if address is None:
            address = address_for(req)
            warnings.append(
                f"the catalogue gave no DestinE Polytope URL for location {name!r} ({url}); "
                f"using {address}"
            )
        for r in _merge(reqs):
            if r in seen:  # replicated at an earlier location: fetched once
                continue
            seen.append(r)
            request = {**_ordered(r, order), **extras}
            out.append({"location": name, "url": url, "address": address, "request": request})
    return out


def validate(req: dict, cat: Catalogue) -> dict:
    """Check a request against the catalogue: values normalised, monthly means with time-mean
    parameters, unavailable values named with the valid ones, and the catalogue's own
    request(s) with the server holding the data. Raises CatalogueUnavailable."""
    sel = selection(req)
    asked = {k: list(v) for k, v in sel.items()}
    order = axis_order(cat, str(req.get("dataset")), sel)
    notes: list[str] = []
    _normalise(sel, cat, notes)
    _resolve_params(sel, cat, notes, order, monthly=sel.get("stream") == ["clmn"])
    _resolve_relative_dates(sel, cat, notes)
    checked = {**req, **{k: "/".join(v) for k, v in sel.items() if v != asked.get(k)}}
    res = {
        "validated": True,
        "request": _ordered(checked, order),
        "requests": [],
        "notes": notes,
        "warnings": [],
        "errors": [],
    }
    found = cat.locations(sel)
    if not found:
        res["errors"] = [_diagnose(cat, sel, order)]
        return res
    entries = [(loc, r) for loc in found for r in loc["requests"]]
    entries, errors, warnings = _coverage(cat, sel, order, entries)
    res["warnings"] += warnings
    if errors:
        res["errors"] = errors
        return res
    res["requests"] = _routed(entries, order, req, res["warnings"])
    return res


def request_document(req: dict, cat: Catalogue) -> dict:
    """What `request` reports: the catalogue's validated request(s), or the offline-linted
    request with a NOT validated warning when the catalogue cannot be reached."""
    res = {
        "validated": False,
        "catalogue": cat.base,
        "collection": COLLECTION,
        "location": None,
        "address": None,
        "request": req,
        "requests": [],
        "notes": [],
        "warnings": [],
        "errors": [_error(m) for m in lint(req)],
        "attribution": attribution(str(req.get("dataset"))),
    }
    if res["errors"]:
        return res
    try:
        res.update(validate(req, cat))
    except CatalogueUnavailable as e:
        status = ecmwf_status.outage_report("destine-catalogue")["summary"]
        res["address"] = address_for(req)
        res["requests"] = [
            {"location": None, "url": None, "address": res["address"], "request": req}
        ]
        res["warnings"].append(
            f"NOT validated: the DestinE catalogue ({cat.base}) could not be reached ({e}). The "
            "request passed the offline checks only — its values, codes and server are "
            f"unverified; run `request` again when the catalogue answers. ECMWF status: {status}"
        )
        return res
    except ValueError as e:
        res["errors"] = [_error(str(e))]
        return res
    if res["requests"]:
        first = res["requests"][0]
        res.update(request=first["request"], address=first["address"], location=first["location"])
        if len(res["requests"]) > 1:
            res["notes"].append(
                f"{len(res['requests'])} requests (the data are split); retrieve fetches each"
            )
    return res


def options_document(dataset: str, axis: str, filters: dict, cat: Catalogue) -> dict:
    """The values one axis has under a selection, with labels and their count."""
    sel = {"class": ["d1"], "dataset": [dataset]}
    if dataset == "climate-dt":
        sel["generation"] = ["2"]  # the generation `request` builds
    sel.update({k: _expand(k, v) for k, v in filters.items()})
    order = axis_order(cat, dataset, sel)
    notes: list[str] = []
    _normalise(sel, cat, notes)
    _resolve_params(sel, cat, notes, order, monthly=False)
    res = {"dataset": dataset, "axis": axis, "selection": sel, "catalogue": cat.base}
    res.update(notes=notes, errors=[])
    axes = cat.axes(sel)
    if not axes:
        res["errors"] = [_diagnose(cat, sel, order)]
    elif axis not in axes:
        res["errors"] = [
            _error(f"{axis} is not an axis of this selection; its axes: {', '.join(axes)}", axis)
        ]
    else:
        values = axes[axis]
        res["count"] = len(values)
        if axis in RANGE_AXES:
            res["ranges"] = ranges(values)
        else:
            res["values"] = [_option(cat, axis, v) for v in values]
        # the values span every value of the axes left open: name them, to narrow down
        res["also_varies"] = {
            k: ranges(axes[k]) if k in RANGE_AXES else axes[k]
            for k in axis_order(cat, dataset, axes)
            if k in axes and k != axis and len(axes[k]) > 1
        }
    return res


# --- retrieve (polytope-client) ------------------------------------------------------------------


def normalise_grid(req: dict) -> dict:
    """grid as the "dx/dy" text Polytope expects: it rejects a list with equal values
    ("Duplicate values found in list for key 'grid'")."""
    g = req.get("grid")
    if isinstance(g, (list, tuple)):
        return {**req, "grid": "/".join(str(v) for v in g)}
    return dict(req)


def retrieve(req: dict, output: str, address: str | None = None) -> None:
    import contextlib

    from polytope.api import Client

    token = read_token()
    with contextlib.redirect_stdout(sys.stderr):
        Client(address=address or address_for(req), user_key=token, quiet=True).retrieve(
            COLLECTION, normalise_grid(req), output
        )


def retrieval_jobs(doc) -> list[tuple[str, dict]]:
    """(server, request) pairs from a `request --json` document, or from a bare request."""
    if not isinstance(doc, dict):
        raise ValueError("the request file must hold a JSON object")
    if "requests" not in doc:
        return [(address_for(doc), doc)]
    if doc.get("errors"):
        raise ValueError("the request has errors — fix them and re-run destine.py request")
    jobs = []
    for e in doc["requests"]:
        req = e.get("request") or {}
        address = e.get("address") or address_for(req)
        if not trusted_address(address):
            raise ValueError(
                f"{address} is not a DestinE Polytope server (polytope.*.apps.dte."
                "destination-earth.eu); the token is only sent there"
            )
        jobs.append((address, req))
    if not jobs:
        raise ValueError("the document lists no request — re-run destine.py request")
    return jobs


def _outputs(output: str, n: int) -> list[str]:
    if n == 1:
        return [output]
    p = Path(output)
    return [str(p.with_name(f"{p.stem}-{i}{p.suffix}")) for i in range(1, n + 1)]


# --- CLI ------------------------------------------------------------------------------------------


def check(as_json: bool, offline: bool = False, env=None, home=None, fetch=_get_json) -> int:
    """Token (name only) and, unless offline, the collections each data bridge lists for it.
    Exit 0 when a bridge lists the Digital Twin collection (or offline with a token), 4 when
    the token is missing, malformed or refused, 2 when no bridge answers."""
    env = os.environ if env is None else env
    src = token_source(env, home)
    res: dict = {"token": src, "addresses": {"lumi": LUMI, "mn5": MN5, "leonardo": LEONARDO}}
    code, barrier = (0, None) if src else (4, None)
    if src:
        try:
            token = read_token(env, home)
        except MalformedToken as e:
            return blocked(malformed_barrier(e), as_json)
        if offline:
            res["collections"] = "not queried (--offline)"
        else:
            cols = collections_by_bridge(token, fetch)
            res["collections"] = cols
            res["digital_twin_access"] = any(
                isinstance(c, list) and COLLECTION in c for c in cols.values()
            )
            if not res["digital_twin_access"]:
                code, barrier = _check_barrier(cols)
    else:
        res["offer"] = (
            "No DestinE token found. Digital Twin data needs upgraded access on the "
            "DestinE Platform. Offer the user step-by-step instructions "
            "(python3 destine.py setup)."
        )
    if barrier:
        res["blocked"] = barrier
        blocked(barrier)  # the report on stderr; stdout keeps one document
    if as_json:
        print(json.dumps(res, indent=2))
    else:
        print("\n".join(f"{k}: {v}" for k, v in res.items() if k != "blocked"))
    return code


def _check_barrier(cols: dict) -> tuple[int, dict]:
    """No bridge lists the collection: why, from the bridges' answers."""
    errors = [c for c in cols.values() if isinstance(c, str)]
    if len(errors) == len(cols):  # every bridge failed: the token, or the network
        offline = ecmwf_status.NETWORK_ERROR
        refused = [e for e in errors if not offline.search(e) and barrier_for_error(e)]
        if refused:
            return 4, barrier_for_error(refused[0])
        return 2, ecmwf_status.network_barrier("destine", errors[0].removeprefix("error: "))
    listed = sorted({x for c in cols.values() if isinstance(c, list) for x in c})
    return 4, {
        "blocked": f"no data bridge lists the '{COLLECTION}' collection for this token",
        "why": f"the token can use: {', '.join(listed) or 'no collections'} — Digital Twin "
        "data needs upgraded access granted by the European Commission.",
        "user_steps": [*setup_steps()[:3], setup_steps()[5]],
        "agent": "Give these steps; offer ecmwf-open-data or ecmwf-cds-ads alternatives meanwhile.",
    }


def _error_text(e: dict) -> str:
    lines = [f"error: {e['message']}"]
    if e.get("options"):
        lines.append(f"  valid {e['axis']} values ({e['count']}):")
        lines += ["    " + "  ".join(x for x in o.values() if x) for o in e["options"]]
    if e.get("ranges"):
        lines.append(f"  available {e['axis']}: {', '.join(e['ranges'])}")
    return "\n".join(lines)


def _print_request(res: dict, as_json: bool) -> int:
    for w in res["warnings"]:
        print(f"WARNING: {w}", file=sys.stderr)
    for e in res["errors"]:
        print(_error_text(e), file=sys.stderr)
    if as_json:
        print(json.dumps(res, indent=2))
        return 2 if res["errors"] else 0
    if res["validated"]:
        print(f"validated by the DestinE catalogue ({res['catalogue']})")
    for n in res["notes"]:
        print(f"note: {n}")
    for e in [] if res["errors"] else res["requests"]:
        where = f"location: {e['location']}  " if e["location"] else ""
        unverified = "" if res["validated"] else "  (not verified)"
        print(f"{where}address: {e['address']}  collection: {COLLECTION}{unverified}")
        print(json.dumps(e["request"], indent=2))
    a = res["attribution"]
    print(f"attribution (DestinE terms of use, section 3.4, verbatim): {a['text']}")
    print(f"  {a['how']}")
    print(f"terms: {a['note']}")
    if a["citation"]:
        print(f"cite: {a['citation']}")
    return 2 if res["errors"] else 0


def _print_options(res: dict, as_json: bool) -> int:
    for e in res["errors"]:
        print(_error_text(e), file=sys.stderr)
    if as_json:
        print(json.dumps(res, indent=2))
        return 2 if res["errors"] else 0
    for n in res["notes"]:
        print(f"note: {n}")
    if not res["errors"]:
        where = ", ".join(f"{k}={'/'.join(v)}" for k, v in res["selection"].items() if k != "class")
        print(f"{res['axis']}: {res['count']} values for {where} (DestinE catalogue)")
        if "ranges" in res:
            print("  " + ", ".join(res["ranges"]))
        for o in res.get("values", []):
            print("  " + "  ".join(x for x in o.values() if x))
        open_axes = [
            f"{k} {'/'.join(v[:8])}" + (f"/… ({len(v)} values)" if len(v) > 8 else "")
            for k, v in res["also_varies"].items()
        ]
        if open_axes:
            print(f"also varies (narrow with --<axis>): {'; '.join(open_axes)}")
    return 2 if res["errors"] else 0


AXIS_FILTERS = (
    "activity",
    "experiment",
    "model",
    "generation",
    "realization",
    "expver",
    "stream",
    "resolution",
    "type",
    "levtype",
    "levelist",
    "param",
    "date",
    "time",
    "step",
    "year",
    "month",
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="token present, and collections per data bridge")
    c.add_argument("--json", action="store_true")
    c.add_argument("--offline", action="store_true", help="don't query the collections endpoint")
    sub.add_parser("setup")
    r = sub.add_parser("request", help="build a request; the DestinE catalogue validates it")
    r.add_argument("dataset", choices=["climate-dt", "extremes-dt"])
    r.add_argument("--param", default="167", help="paramId or short name, e.g. 167 or 2t")
    r.add_argument(
        "--frequency",
        type=str.lower,
        choices=sorted(STREAMS),
        default="hourly",
        help="hourly output (stream clte) or monthly means (clmn: time-mean parameters)",
    )
    r.add_argument("--start", help="YYYY-MM-DD")
    r.add_argument("--end", help="YYYY-MM-DD")
    r.add_argument("--year", help="monthly means: 2040, 2040/2041 or 2040/to/2049")
    r.add_argument("--month", help="monthly means: 7, 6/7/8, 1/to/12 or jul")
    r.add_argument(
        "--resolution",
        type=str.lower,
        choices=["standard", "high"],
        default="standard",
        help="standard (longer availability) or high (limited availability)",
    )
    r.add_argument("--model", default="ifs-fesom", help="ifs-fesom | ifs-nemo | icon")
    r.add_argument("--experiment", default="hist", help="hist | cont | ssp3-7.0 | tplus2.0k")
    r.add_argument(
        "--activity", help="baseline | projections | story-nudging (default: by experiment)"
    )
    r.add_argument("--lat", type=float)
    r.add_argument("--lon", type=float)
    r.add_argument("--json", action="store_true")
    o = sub.add_parser("options", help="valid values, with labels, of one axis under a selection")
    o.add_argument("dataset", choices=["climate-dt", "extremes-dt"])
    o.add_argument("--axis", required=True, help="e.g. param, experiment, model, date, year")
    o.add_argument(
        "--frequency",
        type=str.lower,
        choices=sorted(STREAMS),
        help="hourly (clte) or monthly (clmn)",
    )
    for k in AXIS_FILTERS:
        o.add_argument(f"--{k}", help="keep only data with this value (a/b for several)")
    o.add_argument("--json", action="store_true")
    g = sub.add_parser("retrieve")
    g.add_argument("request", help="JSON from `request --json`, or a bare request")
    g.add_argument("-o", "--output", required=True)
    a = ap.parse_args(_negatives(sys.argv[1:] if argv is None else argv))

    if a.cmd == "setup":
        print("\n".join(setup_steps()))
        return 0
    if a.cmd == "check":
        return check(a.json, a.offline)
    if a.cmd == "request":
        return _request(a)
    if a.cmd == "options":
        filters = {k: getattr(a, k) for k in AXIS_FILTERS if getattr(a, k) is not None}
        if a.frequency and "stream" not in filters:
            filters["stream"] = STREAMS[a.frequency]
        try:
            res = options_document(a.dataset, a.axis, filters, Catalogue())
        except CatalogueUnavailable as e:
            return blocked(ecmwf_status.network_barrier("destine-catalogue", str(e)), a.json, 2)
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        return _print_options(res, a.json)
    return _retrieve(a)


def _request(a) -> int:
    try:
        req = template(
            a.dataset,
            a.param,
            a.start,
            a.end,
            a.model,
            a.experiment,
            a.activity,
            a.lat,
            a.lon,
            a.frequency,
            a.resolution,
            a.year,
            a.month,
        )
    except ValueError as e:
        res = {"validated": False, "request": None, "requests": [], "notes": [], "warnings": []}
        res.update(errors=[_error(str(e))], attribution=attribution(a.dataset))
        return _print_request(res, a.json)
    return _print_request(request_document(req, Catalogue()), a.json)


def _retrieve(a) -> int:
    try:
        jobs = retrieval_jobs(json.loads(Path(a.request).read_text()))
    except (OSError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    errs = [m for _, req in jobs for m in lint(req)]
    if errs:
        print("error: " + "; ".join(errs), file=sys.stderr)
        return 2
    try:
        read_token()
    except MalformedToken as e:
        return blocked(malformed_barrier(e))
    if not token_source():
        return blocked(
            {
                "blocked": "no DestinE token on this machine",
                "why": "Digital Twin data needs upgraded DestinE access and a token in "
                "~/.polytopeapirc-destine.",
                "user_steps": setup_steps(),
                "agent": "Offer these steps once; the request itself can still be "
                "prepared (destine.py request).",
            }
        )
    token = read_token()
    outputs = _outputs(a.output, len(jobs))
    for i, (address, req) in enumerate(jobs):
        b = preflight(req, token, address=address)
        if b:
            return blocked(b)
        try:
            retrieve(req, outputs[i], address)
        except ImportError as e:
            print(f"error: {e}. Run with `uv run destine.py retrieve …`.", file=sys.stderr)
            return 3
        except Exception as e:  # polytope-client errors (401: re-authenticate; 403: no access)
            b = barrier_for_error(str(e))
            if b:
                return blocked(b)
            print(f"error: {str(e).splitlines()[-1] if str(e) else e!r}", file=sys.stderr)
            return 2
        print(f"saved {outputs[i]}")
    print(f"attribution: {attribution(str(jobs[0][1].get('dataset')))['text']}")
    return 0


def _negatives(argv: list[str]) -> list[str]:
    """Accept `--lon -9.14` (argparse would read it as an option)."""
    out, i = [], 0
    while i < len(argv):
        if argv[i] in ("--lat", "--lon") and i + 1 < len(argv) and argv[i + 1].startswith("-"):
            out.append(f"{argv[i]}={argv[i + 1]}")
            i += 2
        else:
            out.append(argv[i])
            i += 1
    return out


if __name__ == "__main__":
    sys.exit(main())
