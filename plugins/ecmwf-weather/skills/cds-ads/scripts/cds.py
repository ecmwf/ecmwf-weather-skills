#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.10"
# dependencies = ["earthkit-data[cds]>=1.2"]
# ///
"""Copernicus Climate (CDS) and Atmosphere (ADS) Data Stores — discover, validate, retrieve.

Everything except `retrieve` is standard library and needs no account (public catalogue and
costing APIs). `retrieve` uses earthkit-data's cds/ads source (`uv run` installs it).

  python3 cds.py check                                     # which keys exist (never printed)
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
        b = key_barrier(store)
        b["blocked"] = f"{store.upper()} rejected the API key"
        b["why"] = "the token is missing, mistyped or for another account."
        return b
    return None


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


def credentials(store: str, env=os.environ, home: Path | None = None) -> str | None:
    """Where the key would come from — never its value. earthkit reads ADS only from ~/.adsapirc."""
    home = home or Path.home()
    if store == "cds":
        if env.get("CDSAPI_KEY"):
            return "CDSAPI_URL/CDSAPI_KEY"
        rc = Path(env.get("CDSAPI_RC", home / ".cdsapirc"))
        if rc.exists() and "ads." not in rc.read_text():
            return "~/.cdsapirc"
        return None
    if store == "ecds":  # same ECMWF token as CDS/ADS; only the url differs
        if env.get("CDSAPI_KEY") or Path(env.get("CDSAPI_RC", home / ".cdsapirc")).exists():
            return "~/.cdsapirc or CDSAPI_KEY (use url https://ecds.ecmwf.int/api via CDSAPI_URL)"
        return None
    if (home / ".adsapirc").exists():
        return "~/.adsapirc"
    rc = home / ".cdsapirc"  # ADS's own instructions put its url/key here
    if rc.exists() and "ads.atmosphere" in rc.read_text():
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


def _token(store: str, env, home: Path) -> str | None:
    if store in ("cds", "ecds") and env.get("CDSAPI_KEY"):
        return env["CDSAPI_KEY"]
    for rc in (
        (home / ".adsapirc", home / ".cdsapirc")
        if store == "ads"
        else (Path(env.get("CDSAPI_RC", home / ".cdsapirc")),)
    ):
        if rc.exists():
            text = rc.read_text()
            if store == "cds" and "ads." in text:
                continue
            m = re.search(r"^key:\s*(\S+)", text, re.M)
            if m:
                return m.group(1)
    return None


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


def licence_status(store: str, dataset: str, form: list, accepted: dict | None) -> dict:
    have = {lic["id"] for lic in (accepted or {}).get("licences", [])}
    need = required_licences(form)
    host = STORES[store]["api"].removesuffix("/api")
    return {
        "required": [lic["label"] for lic in need],
        "missing": [lic["label"] for lic in need if lic["id"] not in have] if accepted else None,
        "accept_at": f"{host}/datasets/{dataset}?tab=download#manage-licences",
    }


def get_form(collection: dict) -> list:
    href = next(
        (link["href"] for link in collection.get("links", []) if link.get("rel") == "form"), None
    )
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


def validate(request: dict, form: list) -> list[str]:
    """Local checks against the dataset form. (Value *combinations* are checked by the server.)"""
    opts, errs = form_options(form), []
    for key, val in request.items():
        o = opts.get(key)
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


def retrieve(store: str, dataset: str, request: dict, output: str) -> None:
    import contextlib

    import earthkit.data as ekd

    with contextlib.redirect_stdout(sys.stderr):
        src = (
            ekd.from_source(store, dataset, request, prompt=False)
            if store == "cds"
            else ekd.from_source(store, dataset, request)
        )
    shutil.copy(src.path, output)


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
            body = e.read()[:300].decode(errors="replace")
            res["errors"].append(f"server rejected the request (HTTP {e.code}): {body}")
            res["valid"] = False
    try:
        res["licences"] = licence_status(store, dataset, form, accepted_licences(store))
    except (urllib.error.URLError, OSError, ValueError):
        res["licences"] = licence_status(store, dataset, form, None)
    if res["licences"]["missing"]:
        res["warnings"] = [
            f"licence not yet accepted: {', '.join(res['licences']['missing'])} — "
            f"accept it at {res['licences']['accept_at']} (Terms of use, bottom), "
            "otherwise the download fails with 403"
        ]
    res["attribution"] = attribution(store)
    res["licence"] = coll.get("license")
    res["doi"] = coll.get("sci:doi")
    return res


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
            res = {
                "cds": credentials("cds"),
                "ads": credentials("ads"),
                "ecds": credentials("ecds"),
                "how_to_get_a_key": KEY_HELP,
                "offer": (
                    "If a key is missing, say once what it would unlock and offer step-by-step "
                    "setup instructions (python3 cds.py setup [--store ads|ecds]); "
                    "give them only if the user accepts."
                ),
                "keyless_alternatives": (
                    "CAMS forecasts: opencharts-wms skill (composition_* WMS layers, "
                    "`wms.py info` for point values)."
                ),
            }
            _print(res, a.json, "\n".join(f"{k}: {v}" for k, v in res.items()))
            return 0 if res["cds"] or res["ads"] else 4

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
        retrieve(a.store, dataset, req, out)
        res["output"] = out
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
