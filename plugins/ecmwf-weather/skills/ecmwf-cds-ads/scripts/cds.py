#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.12"
# dependencies = ["earthkit-data[cds]>=1.2"]
# ///
"""Copernicus Climate (CDS) and Atmosphere (ADS) Data Stores — discover, validate, retrieve.

Everything except `retrieve` is standard library and needs no account (public catalogue and
costing APIs). `retrieve` uses earthkit-data's cds/ads source (`uv run` installs it).
`era5-point` reads ERA5 from ECMWF's OGC API EDR service (standard library, no key) and falls
back to the CDS timeseries dataset (earthkit-data and a CDS key) when the EDR can't serve it.

  python3 cds.py check                                     # keys found and accepted (never printed)
  python3 cds.py search "era5 land"                         # dataset ids
  python3 cds.py describe reanalysis-era5-single-levels     # inputs, licence, DOI, citation
  python3 cds.py validate --dataset ID --request req.json   # local checks + server cost/limit
  python3 cds.py era5-point --lat 38.72 --lon -9.14 --start 1991-01-01 --end 2020-12-31 \
      --variable 2m_temperature -o lisbon.csv              # ERA5 point series (EDR, seconds)
  python3 cds.py era5-point ... --dry-run                   # the plan: EDR URLs, CDS fallback
  uv run  cds.py retrieve --dataset ID --request req.json -o out.nc
  python3 cds.py search "air quality" --store ads           # CAMS datasets
"""

from __future__ import annotations

import argparse
import csv
import difflib
import functools
import http.client
import json
import math
import os
import re
import shutil
import struct
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote, urlencode

# Never write __pycache__ into the installed skill (it may be read-only, and skills must
# not be modified); sibling scripts are imported below.
sys.dont_write_bytecode = True


STORES = {
    "cds": {
        "api": "https://cds.climate.copernicus.eu/api",
        "name": "Climate Data Store",
        "service": "Copernicus Climate Change Service",
        "abbr": "C3S",
        "rc": ".cdsapirc",
    },
    "ecds": {
        "api": "https://ecds.ecmwf.int/api",
        "name": "ECMWF Data Store",
        "service": "ECMWF",
        "abbr": "ECDS",
        "rc": ".cdsapirc",
    },
    "ads": {
        "api": "https://ads.atmosphere.copernicus.eu/api",
        "name": "Atmosphere Data Store",
        "service": "Copernicus Atmosphere Monitoring Service",
        "abbr": "CAMS",
        "rc": ".adsapirc",
    },
}


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


def licence_steps(store: str, dataset: str, labels: list[str]) -> list[str]:
    """Detailed steps to accept dataset licences (only the user can do this)."""
    host = STORES[store]["api"].removesuffix("/api")
    return [
        f"1. Log in at {host} (Login, top right) with your ECMWF account.",
        f"2. Open {host}/datasets/{dataset}?tab=download#manage-licences",
        "3. Scroll to the bottom of the download form, to 'Terms of use'.",
        f"4. Read and click Accept for: {', '.join(labels)}. (Accepted once per account; it "
        "covers every dataset using the same licence.)",
        f"5. Confirm under {host}/profile?tab=licences, then tell the agent to re-run the request.",
    ]


def licence_barrier(store: str, dataset: str, res: dict) -> dict | None:
    missing = (res.get("licences") or {}).get("missing")
    if not missing:
        return None
    return {
        "blocked": f"dataset licence not accepted: {', '.join(missing)}",
        "why": "Copernicus/ECMWF data stores refuse downloads (HTTP 403) until the account "
        "holder accepts the dataset's terms of use — a legal step only the user can do.",
        "user_steps": licence_steps(store, dataset, missing),
        "agent": "Show these steps to the user verbatim; do not retry the download until the "
        "user confirms they accepted; meanwhile offer the validated request.",
    }


def key_barrier(store: str) -> dict:
    return {
        "blocked": f"no {store.upper()} API key",
        "why": "downloads need a personal access token from a (free) ECMWF account.",
        "user_steps": setup_steps(store),
        "agent": "Say once what the key unlocks and offer these steps; give them when the "
        "user accepts. Meanwhile validate the request without a key "
        "(cds.py validate / era5-point --dry-run).",
    }


def barrier_for_error(store: str, dataset: str, message: str) -> dict | None:
    """Map a data-store error to a barrier with instructions (None if not an access barrier)."""
    if re.search(r"(?i)licen[cs]e|terms of use|required licences", message):
        b = licence_barrier(store, dataset, {"licences": {"missing": ["the dataset licence"]}})
        return b
    if re.search(r"(?i)\b401\b|unauthori[sz]ed|invalid (token|key)|authentication", message):
        return key_rejected_barrier(store)
    return None


def key_rejected_barrier(store: str) -> dict:
    b = key_barrier(store)
    b["blocked"] = f"{store.upper()} rejected the API key"
    b["why"] = "the token is missing, mistyped or for another account."
    return b


def setup_steps(store: str = "cds") -> list[str]:
    """Exact steps to obtain and install a CDS/ADS key (verified 2026-10)."""
    s = STORES[store]
    host = s["api"].removesuffix("/api")
    rc = "~/.adsapirc" if store == "ads" else "~/.cdsapirc"
    return [
        f"1. Create a free ECMWF account: open {host}, click Login, then 'Register new user' "
        "(one ECMWF account works for CDS, ADS and the ECMWF Data Store).",
        f"2. Log in and open {host}/how-to-api — it shows your personal access token.",
        f"3. Create {rc} with exactly these two lines, then run: chmod 600 {rc}\n"
        f"   url: {s['api']}\n   key: <your personal access token>"
        + (
            "\n   (CDS also reads the CDSAPI_URL and CDSAPI_KEY environment variables.)"
            if store == "cds"
            else "\n   (The same personal access token as CDS/ADS; keep CDS in ~/.cdsapirc and set "
            "CDSAPI_URL=https://ecds.ecmwf.int/api for ECDS.)"
            if store == "ecds"
            else "\n   (ADS's own page says ~/.cdsapirc; earthkit reads ADS only from ~/.adsapirc.)"
        ),
        "4. Accept the dataset's licence once (downloads fail with HTTP 403 until you do): open "
        f"{host}/datasets/<dataset-id>?tab=download#manage-licences while logged in, scroll to "
        "'Terms of use' at the bottom and click Accept. ERA5 and most Copernicus datasets use "
        "the 'CC-BY licence' — accepting it once covers all of them. Accepted licences are "
        f"listed in your profile: {host}/profile?tab=licences",
        "5. Check: python3 cds.py check",
    ]


KEY_HELP = (
    "Register (free) at https://cds.climate.copernicus.eu "
    "(ADS: https://ads.atmosphere.copernicus.eu), "
    "copy the key from your profile into ~/.cdsapirc (ADS: ~/.adsapirc) as two lines "
    "'url: <store>/api' and 'key: <key>' (CDS also reads CDSAPI_URL/CDSAPI_KEY), and accept "
    "each dataset's licence once on its web page (Download tab)."
)
NON_REQUEST_WIDGETS = {"geo_group", "licences", "global"}
USER_AGENT = "ecmwf-weather-skills/0.1 (+https://github.com/ecmwf/ecmwf-weather-skills)"
TIMEOUT_S = 60  # catalogue documents are < 100 KB


# --- credentials ----------------------------------------------------------------------------------


# Names reported from a broken key file: identifier-like only, so a value that happens to sit
# on a line of its own (a pasted token) is never echoed as a "name".
KEY_NAME = re.compile(r"[A-Za-z_]{1,24}")


class MalformedCredentials(ValueError):
    """A key file exists but holds no usable key. Carries key names, never values."""

    def __init__(self, source: str, problem: str, keys=()):
        self.source, self.problem = source, problem
        self.keys = sorted({str(k) for k in keys if KEY_NAME.fullmatch(str(k))})
        super().__init__(f"{source} is malformed: {problem}")


def _peek(rc: Path) -> str:
    """The file's text for presence checks (undecodable bytes replaced; never raises)."""
    try:
        return rc.read_bytes().decode("utf-8", "replace")
    except OSError:
        return ""


def credentials(store: str, env=os.environ, home: Path | None = None) -> str | None:
    """Where the key would come from — never its value. earthkit reads ADS only from ~/.adsapirc."""
    home = home or Path.home()
    if store == "cds":
        if env.get("CDSAPI_KEY"):
            return "CDSAPI_URL/CDSAPI_KEY"
        rc = Path(env.get("CDSAPI_RC", home / ".cdsapirc"))
        if rc.exists() and "ads." not in _peek(rc):
            return "~/.cdsapirc"
        return None
    if store == "ecds":  # same ECMWF token as CDS/ADS; only the url differs
        if env.get("CDSAPI_KEY") or Path(env.get("CDSAPI_RC", home / ".cdsapirc")).exists():
            return "~/.cdsapirc or CDSAPI_KEY (use url https://ecds.ecmwf.int/api via CDSAPI_URL)"
        return None
    if (home / ".adsapirc").exists():
        return "~/.adsapirc"
    rc = home / ".cdsapirc"  # ADS's own instructions put its url/key here
    if rc.exists() and "ads.atmosphere" in _peek(rc):
        return "~/.cdsapirc (ADS url; earthkit downloads need it copied to ~/.adsapirc)"
    return None


# --- network --------------------------------------------------------------------------------------


def _http(url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def get_collection(store: str, dataset: str) -> dict:
    return _http(f"{STORES[store]['api']}/catalogue/v1/collections/{dataset}")


def required_licences(form: list) -> list[dict]:
    """Licences a dataset's form asks the user to accept (id, revision, label)."""
    out = []
    for w in form:
        if w.get("type") == "LicenceWidget":
            for lic in w.get("details", {}).get("licences", []):
                out.append(
                    {
                        "id": lic["id"],
                        "revision": lic.get("revision"),
                        "label": lic.get("label", lic["id"]),
                    }
                )
    return out


def _rc_key(rc: Path, label: str, text: str | None = None) -> str:
    """The key from a 'url:/key:' rc file or a JSON one ({"url": ..., "key": ...})."""
    if text is None:
        try:
            text = rc.read_bytes().decode("utf-8")
        except OSError as e:
            problem = f"cannot be read ({e.strerror or type(e).__name__})"
            raise MalformedCredentials(label, problem) from None
        except UnicodeDecodeError:
            raise MalformedCredentials(label, "is not UTF-8 text") from None
    if not text.strip():
        raise MalformedCredentials(label, "is empty")
    m = re.search(r"^key:\s*(\S+)", text, re.M)
    if m:
        return m.group(1)
    try:
        d = json.loads(text)
    except json.JSONDecodeError:
        names = re.findall(r"(?m)^\s*([A-Za-z_]{1,24})\s*[:=]", text)
        raise MalformedCredentials(label, "has no 'key:' line (and is not JSON)", names) from None
    if not isinstance(d, dict):
        raise MalformedCredentials(label, f"holds a JSON {type(d).__name__}, not an object")
    key = d.get("key")
    if isinstance(key, str) and key.strip():
        return key
    raise MalformedCredentials(label, "has no key", d.keys())


def _token(store: str, env, home: Path) -> str | None:
    """The store's personal access token; None if no source exists, MalformedCredentials if
    the file that would be used holds none."""
    if store in ("cds", "ecds") and env.get("CDSAPI_KEY"):
        return env["CDSAPI_KEY"]
    if store == "ads":
        files = ((home / ".adsapirc", "~/.adsapirc"), (home / ".cdsapirc", "~/.cdsapirc"))
    elif env.get("CDSAPI_RC"):
        files = ((Path(env["CDSAPI_RC"]), "the file named by CDSAPI_RC"),)
    else:
        files = ((home / ".cdsapirc", "~/.cdsapirc"),)
    for rc, label in files:
        if not rc.exists():
            continue
        ads_url = "ads." in _peek(rc)
        if store == "cds" and ads_url:
            continue
        if store == "ads" and rc.name == ".cdsapirc" and not ads_url:
            continue  # a CDS key: not for ADS
        return _rc_key(rc, label)
    return None


def malformed_barrier(store: str, e: MalformedCredentials) -> dict:
    """BLOCKED report for a key file that exists but holds no usable key (names only)."""
    s = STORES[store]
    host = s["api"].removesuffix("/api")
    f = e.source if e.source.startswith("~/") else "that file"
    return {
        "blocked": f"{e.source} is malformed",
        "why": f"it {e.problem}; keys found: {', '.join(e.keys) or 'none readable'}. The "
        f"{store.upper()} client cannot read a key from it, so every download would fail.",
        "user_steps": [
            f"1. Log in at {host} and open {host}/how-to-api — it shows your personal access "
            "token.",
            f"2. Rewrite {f} as exactly these two lines, then run: chmod 600 {f}\n"
            f"   url: {s['api']}\n   key: <your personal access token>",
            "3. Re-run: python3 cds.py check",
        ],
        "agent": "Relay these steps; never open or print the file and never ask for the token "
        "in chat. Meanwhile validate requests without a key (cds.py validate).",
    }


def _get_status(url: str, headers: dict) -> tuple[int, dict]:
    """(HTTP status, JSON body); HTTP errors are answers, connection failures raise."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def verify_key(store: str, token: str, fetch=_get_status) -> dict:
    """The account's licences endpoint answers only a valid token (a fraction of a second).
    Reports how many licences the account accepted — never the token."""
    status, body = fetch(
        f"{STORES[store]['api']}/profiles/v1/account/licences", {"PRIVATE-TOKEN": token}
    )
    if status == 200:
        return {"verified": True, "accepted_licences": len(body.get("licences", []))}
    return {"verified": False, "status": status}


def _get_with_headers(url: str, headers: dict) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **headers})
    with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
        return json.loads(r.read())


def accepted_licences(
    store: str, env=os.environ, home: Path | None = None, fetch=_get_with_headers
) -> dict | None:
    """The account's accepted licences (needs the key; the key itself is never returned)."""
    token = _token(store, env, home or Path.home())
    if not token:
        return None
    return fetch(f"{STORES[store]['api']}/profiles/v1/account/licences", {"PRIVATE-TOKEN": token})


def licence_status(
    store: str, dataset: str, form: list, accepted: dict | None, reason: str | None = None
) -> dict:
    """state: accepted (confirmed on the account), missing, or unchecked (with the reason)."""
    need = required_licences(form)
    host = STORES[store]["api"].removesuffix("/api")
    res = {
        "required": [lic["label"] for lic in need],
        "accept_at": f"{host}/datasets/{dataset}?tab=download#manage-licences",
        "profile": f"{host}/profile?tab=licences",
    }
    if accepted is None:
        return {
            **res,
            "state": "unchecked",
            "missing": None,
            "reason": reason or "the account's licences could not be read",
        }
    have = {lic["id"] for lic in accepted.get("licences", [])}
    missing = [lic["label"] for lic in need if lic["id"] not in have]
    return {**res, "state": "missing" if missing else "accepted", "missing": missing}


def licence_line(st: dict) -> str:
    """One unambiguous line for the text output — always present."""
    names = ", ".join(st["required"]) or "none required"
    if st["state"] == "accepted":
        return f"licences: accepted — {names} (confirmed on your account)"
    if st["state"] == "missing":
        return f"licences: NOT accepted — {', '.join(st['missing'])} (accept at {st['accept_at']})"
    return (
        f"licences: not checked — {st['reason']}; required: {names}; check {st['profile']} "
        f"or accept at {st['accept_at']}"
    )


def get_form(collection: dict) -> list:
    href = next(
        (link["href"] for link in collection.get("links", []) if link.get("rel") == "form"), None
    )
    if href and not href.startswith("https://"):  # link comes from the server: https only
        raise ValueError(f"catalogue returned a non-https form link: {href}")
    return _http(href) if href else []


def costing(store: str, dataset: str, request: dict) -> dict:
    return _http(
        f"{STORES[store]['api']}/retrieve/v1/processes/{dataset}/costing",
        {"inputs": request},
    )


# --- catalogue ------------------------------------------------------------------------------------


def search_results(search_json: dict, text: str) -> list[dict]:
    words = text.lower().split()
    out = []
    for c in search_json.get("collections", []):
        hay = f"{c['id']} {c.get('title', '')}".lower()
        if all(w in hay for w in words):
            out.append({"id": c["id"], "title": c.get("title"), "licence": c.get("license")})
    return out


def _collect_values(obj) -> list:
    if isinstance(obj, dict):
        vals = list(obj.get("values", [])) if isinstance(obj.get("values"), list) else []
        for k, v in obj.items():
            if k != "values":
                vals += _collect_values(v)
        return vals
    if isinstance(obj, list):
        return [x for item in obj for x in _collect_values(item)]
    return []


def form_options(form: list) -> dict:
    opts = {}
    for w in form:
        name, t, d = w.get("name"), w.get("type", ""), w.get("details", {})
        if not name or name in NON_REQUEST_WIDGETS:
            continue
        if t == "DateRangeWidget":
            opts[name] = {"type": "date", "range": [d.get("minStart"), d.get("maxEnd")]}
        elif t in ("GeographicExtentWidget", "GeographicLocationWidget"):
            opts[name] = {
                "type": "area" if "Extent" in t else "location",
                "range": d.get("range", {}),
            }
        else:
            vals = list(dict.fromkeys(_collect_values(d)))
            opts[name] = {"type": "choice" if "Choice" in t else "list", "values": vals}
    return opts


def attribution(store: str, year: int | None = None) -> str:
    year = year or datetime.now(timezone.utc).year
    if store == "ecds":  # TIGGE/S2S: ECMWF-hosted, not Copernicus; terms in each dataset licence
        return (
            f"Data: © {year} ECMWF and the contributing centres, via the ECMWF Data Store; "
            "used under the dataset licence (cds.py describe shows it)."
        )
    return (
        f"Generated using {STORES[store]['service']} information {year}. Neither the European "
        "Commission nor ECMWF is responsible for any use that may be made of the Copernicus "
        "information or data it contains."
    )


def describe(collection: dict, form: list, store: str) -> dict:
    doi = collection.get("sci:doi")
    s = STORES[store]
    title = collection.get("title", collection.get("id"))
    return {
        "id": collection.get("id"),
        "title": title,
        "store": store,
        "licence": collection.get("license"),
        "doi": doi,
        "temporal": (collection.get("extent", {}).get("temporal", {}).get("interval") or [[None]])[
            0
        ],
        "inputs": {
            k: (
                {**v, "values": v["values"][:40], "n_values": len(v["values"])}
                if "values" in v
                else v
            )
            for k, v in form_options(form).items()
        },
        "citation": f"{s['service']} ({s['abbr']}) {s['name']}: {title}. DOI: {doi}"
        if doi
        else title,
        "attribution": attribution(store),
        "licence_note": (
            "Accept the licence once on the dataset's web page before the API will serve it."
        ),
    }


# --- requests -------------------------------------------------------------------------------------


def _as_list(v):
    return v if isinstance(v, list) else [v]


# Keys the CDS passes to MARS post-processing even when the dataset form doesn't offer them.
# On MARS-backed gridded datasets `grid` regrids to regular lat/lon. `area` alone only works
# where the form offers it: CARRA/CERRA are on Lambert conformal grids that MARS cannot crop
# (the job fails server-side), so there `area` needs `grid` too.
POST_PROCESSING = ("area", "grid")


def _check_post_processing(key: str, val, request: dict, opts: dict) -> list[str]:
    if key == "grid":
        ok = (
            isinstance(val, list)
            and len(val) == 2
            and all(isinstance(v, int | float) and v > 0 for v in val)
        )
        return [] if ok else ["grid must be [dlon, dlat] in degrees, e.g. [0.05, 0.05]"]
    errs = []
    if not (isinstance(val, list) and len(val) == 4):
        errs.append("area must be [north, west, south, east]")
    elif val[0] < val[2]:
        errs.append(f"area north {val[0]} is south of south {val[2]} — order is [N, W, S, E]")
    if "grid" not in request:
        errs.append(
            "this dataset's form has no area input: on Lambert grids (CARRA, CERRA) MARS cannot "
            'crop, and the job fails — add grid, e.g. "grid": [0.05, 0.05], to regrid to '
            "regular lat/lon and crop"
        )
    return errs


def validate(request: dict, form: list) -> list[str]:
    """Local checks against the dataset form. (Value *combinations* are checked by the server.)"""
    opts, errs = form_options(form), []
    for key, val in request.items():
        o = opts.get(key)
        if o is None and key in POST_PROCESSING:
            errs += _check_post_processing(key, val, request, opts)
            continue
        if o is None:
            errs.append(f"unknown key {key!r}; valid keys: {', '.join(opts)}")
            continue
        if o["type"] in ("list", "choice"):
            for v in _as_list(val):
                if o["values"] and str(v) not in o["values"]:
                    near = difflib.get_close_matches(str(v), o["values"], n=3)
                    hint = f" — did you mean {', '.join(near)}?" if near else ""
                    errs.append(f"{key}: {v!r} not allowed{hint}")
        elif o["type"] == "date":
            lo, hi = o["range"]
            for v in _as_list(val):
                for d in str(v).split("/"):
                    if (lo and d < lo) or (hi and d > hi):
                        errs.append(f"date {d} outside the available range {lo} .. {hi}")
        elif o["type"] == "location":
            lat, lon = val.get("latitude"), val.get("longitude")
            if lat is None or not -90 <= lat <= 90:
                errs.append(f"location latitude {lat} must be within -90..90")
            if lon is None or not -180 <= lon <= 180:
                errs.append(f"location longitude {lon} must be within -180..180")
        elif o["type"] == "area":
            if not (isinstance(val, list) and len(val) == 4):
                errs.append("area must be [north, west, south, east]")
            elif val[0] < val[2]:
                errs.append(
                    f"area north {val[0]} is south of south {val[2]} — order is [N, W, S, E]"
                )
    return errs


def era5_point_request(
    lat: float, lon: float, start: str, end: str, variables: list[str], fmt: str = "csv"
) -> dict:
    return {
        "variable": variables,
        "location": {"latitude": lat, "longitude": lon},
        "date": [f"{start}/{end}"],
        "data_format": fmt,
    }


# --- ERA5 point series from ECMWF's OGC API EDR service -------------------------------------------

# ECMWF's OGC API EDR server (pygeoapi). It also serves Destination Earth Digital Twin and
# operational collections: this script only ever requests the ERA5 collection below.
EDR_URL = "https://polytope-edr.lumi.apps.dte.destination-earth.eu"
EDR_COLLECTION = "era5-single-levels"
EDR_NAME = "ECMWF's OGC API EDR service"
ERA5_DATASET = "reanalysis-era5-single-levels"  # the data the EDR serves; cite its DOI
ERA5_DOI = "10.24381/cds.adbb2d47"
TIMESERIES = "reanalysis-era5-single-levels-timeseries"  # the CDS route
# The collection document is ~20 KB and answers in well under a second; a slower answer means
# the service is struggling and the CDS route is the better use of the user's time.
EDR_META_TIMEOUT_S = 15
# The largest request (EDR_MAX_VALUES values, ~21 MB) answered in 4-8 s; allow a slow server.
EDR_DATA_TIMEOUT_S = 120
# Values (hours x variables) per request: 30 years of one hourly variable (262,992 values,
# 21 MB) came back in one request in 4.4 s. Longer requests are split at year boundaries.
EDR_MAX_VALUES = 263_000
# A gridpoint further than one 0.25° grid step from the requested point is a wrong answer.
GRID_STEP_DEG = 0.25
HOUR = timedelta(hours=1)
# CDS variable -> ERA5 short name: the EDR parameter and the column of the CDS CSV.
ERA5_SHORT = {
    "100m_u_component_of_wind": "u100",
    "100m_v_component_of_wind": "v100",
    "10m_u_component_of_wind": "u10",
    "10m_v_component_of_wind": "v10",
    "10m_wind_gust_since_previous_post_processing": "fg10",
    "2m_dewpoint_temperature": "d2m",
    "2m_temperature": "t2m",
    "boundary_layer_height": "blh",
    "cloud_base_height": "cbh",
    "mean_sea_level_pressure": "msl",
    "sea_surface_temperature": "sst",
    "skin_temperature": "skt",
    "surface_latent_heat_flux": "slhf",
    "surface_pressure": "sp",
    "surface_sensible_heat_flux": "sshf",
    "surface_solar_radiation_downwards": "ssrd",
    "surface_thermal_radiation_downwards": "strd",
    "total_cloud_cover": "tcc",
    "total_precipitation": "tp",
    "total_sky_direct_solar_radiation_at_surface": "fdir",
}


class EdrError(Exception):
    """The EDR could not serve the request: an HTTP error, no answer, or a malformed answer."""

    def __init__(self, message: str, status: int | None = None, network: bool = False):
        super().__init__(message)
        self.status, self.network = status, network


class RouteError(ValueError):
    """--route edr asks for something the EDR does not serve."""


class _HttpsOnlyRedirects(urllib.request.HTTPRedirectHandler):
    """Links from servers are followed over https only."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not newurl.startswith("https://"):
            raise urllib.error.URLError(f"refusing redirect to non-https URL {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _edr_get(url: str, timeout: float) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.build_opener(_HttpsOnlyRedirects).open(req, timeout=timeout) as r:
        return r.read()


def _edr_json(fetch, url: str, timeout: float) -> dict:
    """The JSON document at url; every failure becomes an EdrError."""
    try:
        raw = fetch(url, timeout)
    except urllib.error.HTTPError as e:
        raise EdrError(f"the EDR answered HTTP {e.code}", status=e.code) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        why = getattr(e, "reason", None) or e
        raise EdrError(f"the EDR could not be reached ({why})", network=True) from None
    try:
        doc = json.loads(raw)
    except ValueError:
        raise EdrError("the EDR answered with something that is not JSON") from None
    if not isinstance(doc, dict):
        raise EdrError("the EDR answered with unexpected JSON")
    return doc


def _utc(iso: str) -> datetime:
    return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def _day(date: str) -> datetime:
    return datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def edr_collection(fetch=None) -> dict:
    """First and last time and parameters (short name -> unit) of the EDR's ERA5 collection."""
    doc = _edr_json(
        fetch or _edr_get, f"{EDR_URL}/collections/{EDR_COLLECTION}?f=json", EDR_META_TIMEOUT_S
    )
    try:
        first, last = doc["extent"]["temporal"]["interval"][0][:2]
        units = {k: str(v["unit"]["label"]) for k, v in doc["parameter_names"].items()}
        return {"start": _utc(first), "end": _utc(last), "parameters": units}
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise EdrError("the EDR collection document has an unexpected layout") from None


def edr_columns(variables: list[str]) -> tuple[list[str], list[str]]:
    """(CSV columns, variables the EDR lacks). The CDS writes its columns sorted by CDS
    variable name, whatever the request order; the EDR route writes them the same way."""
    names = list(dict.fromkeys(variables))
    columns = [ERA5_SHORT[v] for v in sorted(names) if v in ERA5_SHORT]
    return columns, [v for v in names if v not in ERA5_SHORT]


def edr_chunks(
    start: str, end: str, nvars: int, max_values: int = EDR_MAX_VALUES
) -> list[tuple[datetime, datetime]]:
    """(first hour, last hour) of each request: the whole period when it fits in max_values,
    otherwise as many whole calendar years per request as fit (at least one)."""
    t0, t1 = _day(start), _day(end) + timedelta(hours=23)
    chunks, first, year = [], t0, t0
    while True:
        year_end = min(datetime(year.year, 12, 31, 23, tzinfo=timezone.utc), t1)
        if year != first and ((year_end - first) // HOUR + 1) * nvars > max_values:
            chunks.append((first, year - HOUR))
            first = year
        if year_end == t1:
            return [*chunks, (first, t1)]
        year = datetime(year.year + 1, 1, 1, tzinfo=timezone.utc)


def edr_url(lat: float, lon: float, params: list[str], t0: datetime, t1: datetime) -> str:
    """Position query: the hourly series at the gridpoint nearest (lat, lon), t0 to t1."""
    query = {
        "f": "CoverageJSON",
        "coords": f"POINT({lon!r} {lat!r})",
        "parameter-name": ",".join(params),
        "datetime": f"{_iso(t0)}/{_iso(t1)}",
    }
    path = f"{EDR_URL}/collections/{EDR_COLLECTION}/position?"
    return path + urlencode(query, safe="(),:/", quote_via=quote)


def _static_problem(variables: list[str], fmt: str) -> str | None:
    missing = edr_columns(variables)[1]
    if missing:
        return f"the EDR has no {', '.join(missing)}"
    if fmt != "csv":
        return "the EDR route writes CSV only; NetCDF comes from the CDS"
    return None


def choose_route(
    route: str, variables: list[str], fmt: str, start: str, end: str, collection
) -> tuple[str, str]:
    """('edr' | 'cds', why). auto takes the EDR when it serves every variable for the whole
    period and answered (`collection` is its document, or the EdrError it raised); otherwise
    the CDS. --route edr raises RouteError where auto would take the CDS."""
    if route == "cds":
        return "cds", "requested with --route cds"
    problem = _static_problem(variables, fmt)
    if problem is None and not isinstance(collection, dict):
        problem = str(collection or "the EDR collection was not read")
    if problem is None:
        first, last = collection["start"], collection["end"]
        gone = [c for c in edr_columns(variables)[0] if c not in collection["parameters"]]
        if _day(start) < first:
            problem = f"the period starts before the EDR's first time {first:%Y-%m-%dT%H:%MZ}"
        elif _day(end) + timedelta(hours=23) > last:
            problem = f"the period ends after the EDR's last time {last:%Y-%m-%dT%H:%MZ}"
        elif gone:
            problem = f"the EDR collection does not list {', '.join(gone)}"
        else:
            return "edr", (
                f"the EDR serves every variable and the period ends by its last time "
                f"{last:%Y-%m-%dT%H:%MZ}"
            )
    if route == "edr":
        raise RouteError(f"{problem} — use --route auto (or cds)")
    return "cds", problem


def _hourly_times(t0: datetime, t1: datetime) -> list[str]:
    """Every hour from t0 to t1 as the EDR writes it (2003-08-14T00:00:00Z)."""
    out, day = [], t0.replace(hour=0)
    while day <= t1:
        d = day.strftime("%Y-%m-%d")
        out += [f"{d}T{h:02d}:00:00Z" for h in range(24)]
        day += timedelta(days=1)
    return out[t0.hour : len(out) - (23 - t1.hour)]


def parse_coverage(doc: dict, params: list[str], t0: datetime, t1: datetime) -> dict:
    """An EDR PointSeries checked to hold every parameter for every hour t0..t1:
    {"x", "y", "times", "values": {param: [float | None]}, "units": {param: unit}}."""
    try:
        if doc.get("type") != "Coverage" or doc.get("domainType") != "PointSeries":
            raise EdrError("the EDR did not answer with a point series")
        axes = doc["domain"]["axes"]
        x, y = float(axes["x"]["values"][0]), float(axes["y"]["values"][0])
        times = axes["t"]["values"]
        values, units = {}, {}
        for p in params:
            vals = doc["ranges"][p]["values"]
            if len(vals) != len(times):
                raise EdrError(f"the EDR answered {len(vals)} {p} values for {len(times)} times")
            if any(
                v is not None and (type(v) not in (int, float) or abs(v) > F32_MAX) for v in vals
            ):
                raise EdrError(f"the EDR answered {p} values that are not float32 numbers")
            values[p] = vals
            units[p] = str(doc.get("parameters", {}).get(p, {}).get("unit", {}).get("symbol", ""))
    except (KeyError, IndexError, TypeError, ValueError, AttributeError):
        raise EdrError("the EDR answer has an unexpected layout") from None
    if times != _hourly_times(t0, t1):
        raise EdrError(
            f"the EDR answered {len(times)} of the {(t1 - t0) // HOUR + 1} hours "
            f"{_iso(t0)}..{_iso(t1)}"
        )
    return {"x": x, "y": y, "times": times, "values": values, "units": units}


def _gridpoint(cov: dict, lat: float, lon: float) -> dict:
    """The answer's gridpoint, longitude in -180..180 like the CDS CSV."""
    y, x = cov["y"], (cov["x"] + 180.0) % 360.0 - 180.0
    if abs(y - lat) > GRID_STEP_DEG or abs((x - lon + 180.0) % 360.0 - 180.0) > GRID_STEP_DEG:
        raise EdrError(f"the EDR answered for gridpoint {y}, {x}, not near {lat}, {lon}")
    return {"lat": y, "lon": x}


_F32 = struct.Struct("<f")
_U32 = struct.Struct("<I")
F32_MAX = 3.4028234663852886e38  # ERA5 values are float32


def _f32(v: float) -> float:
    return _F32.unpack(_F32.pack(v))[0]


def _f32_digits(a: float, p: int, pow2: bool) -> str | None:
    """The p-significant-digit decimal (as '%e' text) that reads back as float32 a, or None."""
    s = f"{a:.{p - 1}e}"
    if _f32(float(s)) == a:
        return s
    if pow2:  # below a power of two the float32 spacing halves: the next decimal up may fit
        mant, exp = s.split("e")
        up = f"{int(mant.replace('.', '')) + 1}e{int(exp) - p + 1}"
        if _f32(float(up)) == a:
            return up
    return None


def f32_str(v: float) -> str:
    """str() of the numpy float32 nearest to v — how the CDS writes values into its CSV: the
    shortest digits that read back as the same float32, positional for 1e-4 <= |x| < 1e6,
    otherwise d.ddde±XX."""
    # Cached by bit pattern: as dictionary keys 0.0 and -0.0 are the same, as text they differ.
    return _f32_text(_U32.unpack(_F32.pack(v))[0])


@functools.lru_cache(maxsize=65536)
def _f32_text(bits: int) -> str:
    x = _F32.unpack(_U32.pack(bits))[0]
    if x != x:
        return "nan"
    sign, a = ("-" if math.copysign(1.0, x) < 0 else ""), abs(x)
    if a in (0.0, math.inf):
        return sign + ("0.0" if a == 0.0 else "inf")
    pow2 = bits & 0x7FFFFF == 0
    # Most ERA5 values need 7 or 8 digits: start at 7, then go up or down. 9 digits always
    # identify a float32; fewer digits never fit once a length fails.
    p, best = 7, _f32_digits(a, 7, pow2)
    if best is None:
        best = _f32_digits(a, 8, pow2) or _f32_digits(a, 9, pow2)
    else:
        while p > 1:
            shorter = _f32_digits(a, p - 1, pow2)
            if shorter is None:
                break
            p, best = p - 1, shorter
    mant, e = best.split("e")
    if "." in mant:
        digits, exp = mant.replace(".", "").rstrip("0"), int(e)
    else:  # integer digits from the power-of-two candidate; e is the last digit's exponent
        digits, exp = mant.rstrip("0"), int(e) + len(mant) - 1
    if 1e-4 <= a < 1e6:
        if exp >= 0:
            whole, frac = digits[: exp + 1].ljust(exp + 1, "0"), digits[exp + 1 :] or "0"
        else:
            whole, frac = "0", "0" * (-exp - 1) + digits
        return f"{sign}{whole}.{frac}"
    mant = digits[0] + ("." + digits[1:] if len(digits) > 1 else "")
    return f"{sign}{mant}e{'+' if exp >= 0 else '-'}{abs(exp):02d}"


def _csv_lines(cov: dict, columns: list[str], gridpoint: dict):
    """Rows as the CDS writes them: 2003-08-14 00:00:00,<values>,<lat>,<lon>; missing empty."""
    where = f"{gridpoint['lat']!r},{gridpoint['lon']!r}\n"
    series = [cov["values"][c] for c in columns]
    for i, t in enumerate(cov["times"]):
        cells = ("" if s[i] is None or s[i] != s[i] else f32_str(s[i]) for s in series)
        yield f"{t[:10]} {t[11:19]},{','.join(cells)},{where}"


def fetch_edr(
    lat: float, lon: float, variables: list[str], start: str, end: str, output, fetch=None, log=None
) -> dict:
    """Download ERA5 hourly values at the gridpoint nearest (lat, lon) from the EDR and write
    them as the CDS timeseries route writes its CSV. Raises EdrError; never leaves a partial
    file behind."""
    fetch = fetch or _edr_get
    columns = edr_columns(variables)[0]
    chunks = edr_chunks(start, end, len(columns))
    output = Path(output)
    part = output.with_name(output.name + ".part")
    began, gridpoint, units, rows, done = time.monotonic(), None, {}, 0, False
    try:
        with open(part, "w", newline="", encoding="utf-8") as f:
            f.write(",".join(["valid_time", *columns, "latitude", "longitude"]) + "\n")
            for n, (t0, t1) in enumerate(chunks, 1):
                t = time.monotonic()
                doc = _edr_json(fetch, edr_url(lat, lon, columns, t0, t1), EDR_DATA_TIMEOUT_S)
                cov = parse_coverage(doc, columns, t0, t1)
                gp = _gridpoint(cov, lat, lon)
                if gridpoint not in (None, gp):
                    raise EdrError("the EDR answered for different gridpoints across requests")
                gridpoint, units = gp, units or cov["units"]
                f.writelines(_csv_lines(cov, columns, gp))
                rows += len(cov["times"])
                if log and len(chunks) > 1:
                    log(
                        f"EDR request {n}/{len(chunks)} ({t0:%Y-%m-%d}..{t1:%Y-%m-%d}): "
                        f"{time.monotonic() - t:.1f} s"
                    )
        os.replace(part, output)
        done = True
    finally:
        if not done:
            part.unlink(missing_ok=True)
    return {
        "rows": rows,
        "requests": len(chunks),
        "gridpoint": gridpoint,
        "units": units,
        "variables": columns,
        "seconds": round(time.monotonic() - began, 1),
    }


def edr_barrier(e: EdrError) -> dict:
    """BLOCKED report for --route edr when the EDR refuses or breaks (not unreachable)."""
    return {
        "blocked": f"the ERA5 route through {EDR_NAME} failed: {e}",
        "why": "the EDR is a fast, keyless route to ERA5 whose access terms are under review by "
        "its owners, so it may change or close; the same ERA5 data is on the CDS.",
        "user_steps": [
            "1. Nothing to set up for the EDR. Use the CDS route: re-run without --route (auto "
            "falls back to the CDS by itself) or with --route cds.",
            "2. The CDS route needs a free CDS key and the ERA5 licence ('CC-BY licence') "
            "accepted once — python3 cds.py setup prints the steps.",
            "3. If the EDR keeps refusing, ask https://support.ecmwf.int whether it moved.",
        ],
        "agent": "Re-run the same command with --route auto (or --route cds); don't retry "
        "--route edr until the user asks.",
    }


def edr_blocked(e: EdrError, as_json: bool) -> int:
    """--route edr failed: unreachable or a server error (exit 2), refused (4), broken (2)."""
    if e.network or (e.status is not None and (e.status >= 500 or e.status == 429)):
        import ecmwf_status  # sibling script

        return blocked(ecmwf_status.network_barrier("era5-edr", str(e)), as_json, code=2)
    return blocked(edr_barrier(e), as_json, code=4 if e.status in (401, 403) else 2)


def csv_gridpoint(path) -> dict | None:
    """The gridpoint the CDS chose: latitude and longitude of its CSV's first row."""
    try:
        with open(path, newline="", encoding="utf-8") as f:
            rows = csv.reader(f)
            head, first = next(rows), next(rows)
        return {
            "lat": float(first[head.index("latitude")]),
            "lon": float(first[head.index("longitude")]),
        }
    except (OSError, StopIteration, ValueError, IndexError):
        return None


# --- retrieve (earthkit) --------------------------------------------------------------------------


def unpack_download(src: Path, output: Path) -> list[Path]:
    """Data stores often deliver a ZIP (e.g. ERA5 time series CSV, netcdf_zip). A single member
    becomes `output`; several are extracted into a directory named after `output`."""
    import zipfile

    src, output = Path(src), Path(output)
    if not zipfile.is_zipfile(src):
        shutil.copy(src, output)
        return [output]
    with zipfile.ZipFile(src) as z:
        members = [m for m in z.infolist() if not m.is_dir()]
        for m in members:
            if Path(m.filename).is_absolute() or ".." in Path(m.filename).parts:
                raise ValueError(f"unsafe path in downloaded archive: {m.filename}")
        if len(members) == 1:
            output.write_bytes(z.read(members[0]))
            return [output]
        out_dir = output.with_suffix("")
        out_dir.mkdir(parents=True, exist_ok=True)
        files = []
        for m in members:
            target = out_dir / Path(m.filename).name
            target.write_bytes(z.read(m))
            files.append(target)
        return sorted(files)


def retrieve(store: str, dataset: str, request: dict, output: str) -> list[Path]:
    import contextlib

    import earthkit.data as ekd

    with contextlib.redirect_stdout(sys.stderr):
        src = (
            ekd.from_source(store, dataset, request, prompt=False)
            if store == "cds"
            else ekd.from_source(store, dataset, request)
        )
    return unpack_download(Path(src.path), Path(output))


# --- CLI ------------------------------------------------------------------------------------------


def _print(obj, as_json: bool, text: str):
    print(json.dumps(obj, indent=2) if as_json else text)


def apply_costing(res: dict, c: dict) -> dict:
    """Record the server's costing and turn its verdict (size limit, validity) into errors."""
    res["cost"] = c
    if c.get("request_is_valid") is False:
        res["valid"] = False
        res["errors"].append(f"server rejected the request: {c.get('invalid_reason', '?')}")
    if c.get("cost", 0) > c.get("limit", float("inf")):
        res["valid"] = False
        res["errors"].append(
            f"request too large: cost {c['cost']} > limit {c['limit']} — split it "
            "(e.g. by year) or shrink area/variables"
        )
    return res


def _validate_and_cost(store, dataset, request) -> dict:
    coll = get_collection(store, dataset)
    form = get_form(coll)
    errs = validate(request, form)
    res = {
        "dataset": dataset,
        "store": store,
        "request": request,
        "valid": not errs,
        "errors": errs,
    }
    if not errs:
        try:
            apply_costing(res, costing(store, dataset, request))
        except urllib.error.HTTPError as e:
            if e.code == 429 or e.code >= 500:  # the costing service is down, not the request
                res["cost"] = None
                res.setdefault("warnings", []).append(
                    f"size limit could not be checked (costing service answered HTTP {e.code}); "
                    "the request is locally valid — re-run validate before a large download"
                )
            else:
                body = e.read()[:300].decode(errors="replace")
                res["errors"].append(f"server rejected the request (HTTP {e.code}): {body}")
                res["valid"] = False
    try:
        accepted = accepted_licences(store)
        reason = (
            None
            if accepted is not None
            else (
                f"no {store.upper()} key readable by this script (~/.cdsapirc / CDSAPI_KEY"
                + (" / ~/.adsapirc" if store == "ads" else "")
                + ")"
            )
        )
        res["licences"] = licence_status(store, dataset, form, accepted, reason)
    except (urllib.error.URLError, OSError, ValueError) as e:
        res["licences"] = licence_status(
            store, dataset, form, None, f"the account's licences could not be read ({e})"
        )
    if res["licences"]["missing"]:
        res.setdefault("warnings", []).extend(
            [
                f"licence not yet accepted: {', '.join(res['licences']['missing'])} — "
                f"accept it at {res['licences']['accept_at']} (Terms of use, bottom), "
                "otherwise the download fails with 403"
            ]
        )
    res["attribution"] = attribution(store)
    res["licence"] = coll.get("license")
    res["doi"] = coll.get("sci:doi")
    return res


def validation_text(dataset: str, req: dict, res: dict, with_attribution: bool = True) -> str:
    return "\n".join(
        [
            f"dataset: {dataset}",
            "request: " + json.dumps(req),
            "valid" if res["valid"] else "INVALID:\n  " + "\n  ".join(res["errors"]),
            f"cost: {res.get('cost')}",
            licence_line(res["licences"]),
            *[f"WARNING: {w}" for w in res.get("warnings", [])],
            *([f"attribution: {res['attribution']}"] if with_attribution else []),
        ]
    )


def report_validation(store, dataset, req, res, as_json: bool, head=()) -> int:
    """Print a validation result, and the licence report when a licence is missing."""
    _print(res, as_json, "\n".join([*head, validation_text(dataset, req, res)]))
    lb = licence_barrier(store, dataset, res)
    if lb:  # the request is fine but the account can't download it yet
        blocked(lb)
    return 0 if res["valid"] else 2


# --- era5-point: the EDR route or the CDS route ---------------------------------------------------


def _point_errors(a, variables: list[str]) -> list[str]:
    errs = []
    try:
        if _day(a.start) > _day(a.end):
            errs.append(f"--start {a.start} is after --end {a.end}")
    except ValueError:
        errs.append(f"--start and --end must be dates like 2003-08-01 (got {a.start}, {a.end})")
    if not -90 <= a.lat <= 90:
        errs.append(f"location latitude {a.lat} must be within -90..90")
    if not -180 <= a.lon <= 180:
        errs.append(f"location longitude {a.lon} must be within -180..180")
    if not variables:
        errs.append("--variable names no variable")
    return errs


def era5_point(a) -> int:
    """ERA5 hourly series at a point: the EDR when it serves the request (seconds, no key),
    otherwise the CDS timeseries dataset (queued, key). --route picks one explicitly."""
    variables = [v.strip() for v in a.variable.split(",") if v.strip()]
    errs = _point_errors(a, variables)
    if errs:
        print("error: " + "; ".join(errs), file=sys.stderr)
        return 2
    collection = None
    if a.route != "cds" and _static_problem(variables, a.format) is None:
        try:
            collection = edr_collection()
        except EdrError as e:
            if a.route == "edr":
                return edr_blocked(e, a.json)
            collection = e
    try:
        route, why = choose_route(a.route, variables, a.format, a.start, a.end, collection)
    except RouteError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if route == "cds":
        if a.route == "auto":
            print(f"note: using the CDS route — {why}", file=sys.stderr)
        return era5_point_cds(a, variables, why)
    if a.dry_run:
        return era5_point_plan(a, variables, collection, why)
    out = Path(a.output or f"{TIMESERIES}.csv")
    try:
        r = fetch_edr(
            a.lat, a.lon, variables, a.start, a.end, out, log=lambda m: print(m, file=sys.stderr)
        )
    except EdrError as e:
        if a.route == "edr":
            return edr_blocked(e, a.json)
        why = f"{e} — the CDS route serves the same ERA5 data"
        print(f"note: falling back to the CDS route — {why}", file=sys.stderr)
        return era5_point_cds(a, variables, why)
    gp = r["gridpoint"]
    res = {
        "route": "edr",
        "route_reason": why,
        "service": f"{EDR_URL}/collections/{EDR_COLLECTION}",
        "dataset": ERA5_DATASET,
        "location": {"lat": a.lat, "lon": a.lon},
        "gridpoint": gp,
        "variables": r["variables"],
        "units": r["units"],
        "rows": r["rows"],
        "requests": r["requests"],
        "seconds": r["seconds"],
        "output": [str(out)],
        "attribution": attribution("cds"),
        "licence": "CC-BY-4.0",
        "doi": ERA5_DOI,
    }
    text = [
        f"saved {out} — {r['rows']} hourly rows of {', '.join(r['variables'])}",
        f"route: edr — {EDR_NAME}, no CDS key needed ({r['requests']} request(s), "
        f"{r['seconds']} s); {why}",
        f"gridpoint: {gp['lat']}, {gp['lon']} (nearest ERA5 gridpoint to {a.lat}, {a.lon})",
        f"attribution: {res['attribution']}",
        f"DOI: {ERA5_DOI} (ERA5 hourly data on single levels)",
    ]
    _print(res, a.json, "\n".join(text))
    return 0


def era5_point_plan(a, variables: list[str], collection: dict, why: str) -> int:
    """--dry-run on the EDR route: the requests it would send (and, for auto, the validated CDS
    request it would fall back to). Reads no data."""
    columns = edr_columns(variables)[0]
    chunks = edr_chunks(a.start, a.end, len(columns))
    plan = {
        "service": f"{EDR_URL}/collections/{EDR_COLLECTION}",
        "first_time": _iso(collection["start"]),
        "last_time": _iso(collection["end"]),
        "parameters": columns,
        "requests": [
            {"start": _iso(t0), "end": _iso(t1), "url": edr_url(a.lat, a.lon, columns, t0, t1)}
            for t0, t1 in chunks
        ],
        "attribution": attribution("cds"),
        "licence": "CC-BY-4.0",
        "doi": ERA5_DOI,
    }
    res = {"route": "edr", "route_reason": why, "edr": plan}
    shown = 5  # the JSON lists every request
    text = [
        f"route: edr — {why}",
        f"plan: {len(chunks)} request(s) to {EDR_NAME}, no key needed (ERA5 to "
        f"{plan['last_time']}):",
        *[f"  {q['url']}" for q in plan["requests"][:shown]],
        *([f"  … {len(chunks) - shown} more (--json lists them)"] if len(chunks) > shown else []),
        f"attribution: {plan['attribution']}",
        f"DOI (EDR route): {ERA5_DOI} (ERA5 hourly data on single levels)",
    ]
    if a.route == "auto":  # what the automatic CDS fallback would request
        req = era5_point_request(a.lat, a.lon, a.start, a.end, variables, a.format)
        try:
            res["cds"] = _validate_and_cost(a.store, TIMESERIES, req)
            text += [
                "CDS fallback (used by itself if the EDR fails; needs a CDS key):",
                validation_text(TIMESERIES, req, res["cds"], with_attribution=False),
                f"DOI (CDS route): {res['cds']['doi']}",
            ]
        except (urllib.error.URLError, OSError, ValueError) as e:
            res["cds"] = {"error": f"the CDS fallback could not be validated ({e})"}
            text.append(f"CDS fallback: {res['cds']['error']}")
    _print(res, a.json, "\n".join(text))
    return 0


def era5_point_cds(a, variables: list[str], why: str) -> int:
    """The CDS route: reanalysis-era5-single-levels-timeseries (queued; a key and earthkit)."""
    req = era5_point_request(a.lat, a.lon, a.start, a.end, variables, a.format)
    head = [f"route: cds — {why}"]
    if not a.dry_run and not credentials(a.store):
        return blocked(key_barrier(a.store), a.json)
    res = {"route": "cds", "route_reason": why, **_validate_and_cost(a.store, TIMESERIES, req)}
    if a.dry_run or not res["valid"]:
        return report_validation(a.store, TIMESERIES, req, res, a.json, head)
    lb = licence_barrier(a.store, TIMESERIES, res)
    if lb:
        return blocked(lb, a.json)
    out = a.output or f"{TIMESERIES}.{'csv' if a.format == 'csv' else 'nc'}"
    files = retrieve(a.store, TIMESERIES, req, out)
    res["output"] = [str(f) for f in files]
    gp = csv_gridpoint(files[0]) if a.format == "csv" else None
    text = [f"saved {', '.join(res['output'])}", *head]
    if gp:
        res["gridpoint"] = gp
        text.append(
            f"gridpoint: {gp['lat']}, {gp['lon']} (nearest ERA5 gridpoint to {a.lat}, {a.lon})"
        )
    text += [f"attribution: {res['attribution']}", f"DOI: {res['doi']}"]
    _print(res, a.json, "\n".join(text))
    return 0


def check(as_json: bool, env=None, home: Path | None = None, fetch=_get_status) -> int:
    """Which keys exist (names only) and whether each store accepts its key. Exit 0 a key
    works, 4 none / malformed / rejected, 2 the store could not be reached."""
    env = os.environ if env is None else env
    home = home or Path.home()
    res: dict = {
        "cds": credentials("cds", env, home),
        "ads": credentials("ads", env, home),
        "ecds": credentials("ecds", env, home),
        "how_to_get_a_key": KEY_HELP,
        "offer": (
            "If a key is missing, say once what it would unlock and offer step-by-step "
            "setup instructions (python3 cds.py setup [--store ads|ecds]); "
            "give them only if the user accepts."
        ),
        "keyless_alternatives": (
            "CAMS forecasts: ecmwf-opencharts-wms skill (composition_* WMS layers, "
            "`wms.py info` for point values)."
        ),
    }
    code, barrier = (0 if res["cds"] or res["ads"] else 4), None
    for store in ("cds", "ads"):
        if not res[store]:
            continue
        try:
            token = _token(store, env, home)
        except MalformedCredentials as e:
            return blocked(malformed_barrier(store, e), as_json)
        if token is None:  # a source the downloads would not use (e.g. ADS url in ~/.cdsapirc)
            continue
        try:
            res[f"{store}_key"] = verify_key(store, token, fetch)
        except (urllib.error.URLError, OSError) as e:
            import ecmwf_status  # sibling script

            res[f"{store}_key"] = {"verified": None, "error": str(e)[:200]}
            code, barrier = 2, barrier or ecmwf_status.network_barrier(store, str(e))
            continue
        if not res[f"{store}_key"]["verified"]:
            code, barrier = 4, key_rejected_barrier(store)
    if barrier:
        res["blocked"] = barrier
        blocked(barrier)  # the report on stderr; stdout keeps one document
    _print(res, as_json, "\n".join(f"{k}: {v}" for k, v in res.items() if k != "blocked"))
    return code


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, **kw):
        sp = sub.add_parser(name, **kw)
        sp.add_argument("--store", choices=list(STORES), default="cds")
        sp.add_argument("--json", action="store_true")
        return sp

    add("check")
    add("setup", help="step-by-step instructions to obtain and install a key")
    sp = add("search")
    sp.add_argument("text")
    sp = add("describe")
    sp.add_argument("dataset")
    for name in ("validate", "retrieve"):
        sp = add(name)
        sp.add_argument("--dataset", required=True)
        sp.add_argument("--request", required=True, help="JSON file with the request")
        if name == "retrieve":
            sp.add_argument("-o", "--output", required=True)
    sp = add(
        "era5-point",
        help="ERA5 hourly time series at a point: ECMWF's EDR service (seconds, no key) or "
        f"the CDS ({TIMESERIES})",
    )
    sp.add_argument("--lat", type=float, required=True)
    sp.add_argument("--lon", type=float, required=True)
    sp.add_argument("--start", required=True, help="YYYY-MM-DD (from 1940-01-01)")
    sp.add_argument(
        "--end", required=True, help="YYYY-MM-DD (the EDR ends ~10 days ago, the CDS ~5)"
    )
    sp.add_argument(
        "--variable",
        default="2m_temperature",
        help="comma list, e.g. 2m_temperature,total_precipitation",
    )
    sp.add_argument("--format", default="csv", choices=["csv", "netcdf"])
    sp.add_argument(
        "--route",
        choices=["auto", "edr", "cds"],
        default="auto",
        help="auto: the EDR when it serves every variable for the whole period, else the CDS "
        "(also when the EDR fails); edr / cds: only that route",
    )
    sp.add_argument(
        "--dry-run", action="store_true", help="the plan only: EDR URLs, CDS request validated"
    )
    sp.add_argument("-o", "--output")
    a = ap.parse_args(argv)

    try:
        if a.cmd == "setup":
            print("\n".join(setup_steps(a.store)))
            return 0

        if a.cmd == "check":
            return check(a.json)

        if a.cmd == "search":
            s = _http(
                f"{STORES[a.store]['api']}/catalogue/v1/collections?q={quote(a.text)}&limit=100"
            )
            hits = search_results(s, a.text) or [
                {"id": c["id"], "title": c.get("title"), "licence": c.get("license")}
                for c in s.get("collections", [])
            ]
            _print(hits, a.json, "\n".join(f"{h['id']:55} {h['title']}" for h in hits[:40]))
            return 0

        if a.cmd == "describe":
            coll = get_collection(a.store, a.dataset)
            d = describe(coll, get_form(coll), a.store)
            text = "\n".join(
                [
                    f"{d['title']} ({d['id']})",
                    f"licence: {d['licence']}   DOI: {d['doi']}",
                    f"period: {d['temporal']}",
                ]
                + [
                    f"  {k}: "
                    + (
                        ", ".join(map(str, v.get("values", [])[:12]))
                        + (" …" if v.get("n_values", 0) > 12 else "")
                        if "values" in v
                        else str(v.get("range"))
                    )
                    for k, v in d["inputs"].items()
                ]
                + [
                    f"citation: {d['citation']}",
                    f"attribution: {d['attribution']}",
                    d["licence_note"],
                ]
            )
            _print(d, a.json, text)
            return 0

        if a.cmd == "era5-point":
            return era5_point(a)

        req = json.loads(Path(a.request).read_text())
        dataset = a.dataset
        if a.cmd == "retrieve" and not credentials(a.store):
            return blocked(key_barrier(a.store), a.json)

        res = _validate_and_cost(a.store, dataset, req)
        if a.cmd == "validate" or not res["valid"]:
            return report_validation(a.store, dataset, req, res, a.json)

        lb = licence_barrier(a.store, dataset, res)
        if lb:
            return blocked(lb, a.json)

        out = a.output or f"{dataset}.{'csv' if req.get('data_format') == 'csv' else 'nc'}"
        files = retrieve(a.store, dataset, req, out)
        res["output"] = [str(f) for f in files]
        out = ", ".join(res["output"])
        _print(
            res,
            a.json,
            f"saved {out}\nattribution: {res['attribution']}\nDOI: {res['doi']}",
        )
        return 0
    except ImportError as e:
        print(
            f"error: {e}. Run with `uv run cds.py …` (installs earthkit-data[cds]).",
            file=sys.stderr,
        )
        return 3
    except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
        if isinstance(e, (urllib.error.URLError, ConnectionError, TimeoutError)) and not isinstance(
            e, urllib.error.HTTPError
        ):
            import ecmwf_status  # sibling script

            return blocked(ecmwf_status.network_barrier(a.store, str(e)), a.json, code=2)
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # earthkit/cdsapi errors: licence not accepted, queue failures
        msg = str(e)
        dataset = getattr(a, "dataset", None) or (
            TIMESERIES if a.cmd == "era5-point" else "<dataset-id>"
        )
        b = barrier_for_error(a.store, dataset, msg)
        if b:
            return blocked(b, a.json)
        print(f"error: {msg}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
