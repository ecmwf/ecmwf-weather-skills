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

  python3 cds.py check                                     # keys found and accepted (never printed)
  python3 cds.py search "era5 land"                         # dataset ids
  python3 cds.py describe reanalysis-era5-single-levels     # inputs, licence, DOI, citation
  python3 cds.py validate --dataset ID --request req.json   # local checks + server cost/limit
  python3 cds.py era5-point --lat 38.72 --lon -9.14 --start 1991-01-01 --end 2020-12-31 \
      --variable 2m_temperature --dry-run                   # ERA5 point time series request
  uv run  cds.py retrieve --dataset ID --request req.json -o out.nc
  python3 cds.py search "air quality" --store ads           # CAMS datasets
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import shutil
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

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
        help="ERA5 hourly time series at a point (reanalysis-era5-single-levels-timeseries)",
    )
    sp.add_argument("--lat", type=float, required=True)
    sp.add_argument("--lon", type=float, required=True)
    sp.add_argument("--start", required=True, help="YYYY-MM-DD (from 1940-01-01)")
    sp.add_argument("--end", required=True, help="YYYY-MM-DD (to ~5 days ago)")
    sp.add_argument(
        "--variable",
        default="2m_temperature",
        help="comma list, e.g. 2m_temperature,total_precipitation",
    )
    sp.add_argument("--format", default="csv", choices=["csv", "netcdf"])
    sp.add_argument("--dry-run", action="store_true", help="validate and cost only")
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
            req = era5_point_request(a.lat, a.lon, a.start, a.end, a.variable.split(","), a.format)
            dataset = "reanalysis-era5-single-levels-timeseries"
        else:
            req = json.loads(Path(a.request).read_text())
            dataset = a.dataset

        needs_key = a.cmd == "retrieve" or (a.cmd == "era5-point" and not a.dry_run)
        if needs_key and not credentials(a.store):
            return blocked(key_barrier(a.store), a.json)

        res = _validate_and_cost(a.store, dataset, req)
        if a.cmd == "validate" or (a.cmd == "era5-point" and a.dry_run) or not res["valid"]:
            text = "\n".join(
                [
                    f"dataset: {dataset}",
                    "request: " + json.dumps(req),
                    "valid" if res["valid"] else "INVALID:\n  " + "\n  ".join(res["errors"]),
                    f"cost: {res.get('cost')}",
                    licence_line(res["licences"]),
                    *[f"WARNING: {w}" for w in res.get("warnings", [])],
                    f"attribution: {res['attribution']}",
                ]
            )
            _print(res, a.json, text)
            lb = licence_barrier(a.store, dataset, res)
            if lb:  # the request is fine but the account can't download it yet
                blocked(lb)
            return 0 if res["valid"] else 2

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
        b = barrier_for_error(a.store, getattr(a, "dataset", None) or "<dataset-id>", msg)
        if b:
            return blocked(b, a.json)
        print(f"error: {msg}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
