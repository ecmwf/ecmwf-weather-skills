#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
# /// script
# requires-python = ">=3.10"
# dependencies = ["polytope-client>=0.7"]
# ///
"""Destination Earth (DestinE) Digital Twin data via the DestinE Polytope service.

check/setup/request run on plain Python 3; retrieve needs uv (installs polytope-client).
Digital Twin data needs UPGRADED access on the DestinE Platform — `setup` explains how to
request it. The token lives in ~/.polytopeapirc-destine (or DESTINE_POLYTOPE_KEY) so it never
overwrites an ECMWF Polytope key in ~/.polytopeapirc.

  python3 destine.py check                     # token present? (never printed)
  python3 destine.py setup                     # how to request access and authenticate
  python3 destine.py request climate-dt --param 167 --start 2014-01-01 --end 2014-01-31
  python3 destine.py request extremes-dt --param 167 --lat 38.72 --lon -9.14
  uv run  destine.py retrieve req.json -o out.grib      # (.covjson for feature requests)

Request templates follow the official polytope-examples; live verification is pending.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

TOKEN_FILE = ".polytopeapirc-destine"
TOKEN_ENV = "DESTINE_POLYTOPE_KEY"
LUMI = "polytope.lumi.apps.dte.destination-earth.eu"
MN5 = "polytope.mn5.apps.dte.destination-earth.eu"
LEONARDO = "polytope.leonardo.apps.dte.destination-earth.eu"
COLLECTION = "destination-earth"
DATASETS = ("climate-dt", "extremes-dt", "on-demand-extremes-dt")
CLIMATE_DT_DOI = "10.21957/79c6af3105"


# --- credentials ----------------------------------------------------------------------------------


def token_source(env=os.environ, home: Path | None = None) -> str | None:
    """Where the DestinE token comes from — never its value."""
    home = home or Path.home()
    if env.get(TOKEN_ENV):
        return TOKEN_ENV
    if (home / TOKEN_FILE).exists():
        return f"~/{TOKEN_FILE}"
    return None


def read_token(env=os.environ, home: Path | None = None) -> str | None:
    home = home or Path.home()
    if env.get(TOKEN_ENV):
        return env[TOKEN_ENV]
    f = home / TOKEN_FILE
    return json.loads(f.read_text()).get("user_key") if f.exists() else None


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


# --- requests -------------------------------------------------------------------------------------


def address_for(req: dict) -> str:
    """Which data bridge holds the data (Climate DT user guide, 'Data Structure and Keys')."""
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


def template(
    dataset: str,
    param: str = "167",
    start: str | None = None,
    end: str | None = None,
    model: str = "ifs-fesom",
    experiment: str = "hist",
    activity: str = "baseline",
    lat: float | None = None,
    lon: float | None = None,
) -> dict:
    if dataset == "climate-dt":
        start, end = start or "2014-01-01", end or "2014-01-31"
        r = {
            "class": "d1",
            "dataset": "climate-dt",
            "activity": activity,
            "experiment": experiment,
            "generation": "2",
            "model": model,
            "realization": "1",
            "resolution": "high",
            "expver": "0001",
            "stream": "clte",
            "type": "fc",
            "levtype": "sfc",
            "date": f"{start.replace('-', '')}/to/{end.replace('-', '')}",
            "time": "0000/to/2300",
            "param": param,
        }
        f = _feature(lat, lon, "date")
    elif dataset == "extremes-dt":
        r = {
            "class": "d1",
            "dataset": "extremes-dt",
            "expver": "0001",
            "stream": "oper",
            "type": "fc",
            "date": "-1",
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
    return errs


def attribution(dataset: str) -> dict:
    a = {
        "text": "This service/application/tool/data is created based on data of the European "
        "Commission and/or using the Destination Earth Platform (modified by <you>).",
        "note": "Upgraded-access data is for official use; raw data may not be redistributed "
        "without the European Commission's written consent; only derived products from "
        "which the original data cannot be recovered may be shared.",
        "citation": "",
    }
    if dataset == "climate-dt":
        a["citation"] = (
            "Destination Earth Digital Twin for Climate Change Adaptation (DestinE "
            f"Climate DT Generation 2), ECMWF, https://doi.org/{CLIMATE_DT_DOI}"
        )
    return a


# --- retrieve (polytope-client) ------------------------------------------------------------------


def retrieve(req: dict, output: str) -> None:
    import contextlib

    from polytope.api import Client

    token = read_token()
    with contextlib.redirect_stdout(sys.stderr):
        Client(address=address_for(req), user_key=token, quiet=True).retrieve(
            COLLECTION, req, output
        )


# --- CLI ------------------------------------------------------------------------------------------


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").add_argument("--json", action="store_true")
    sub.add_parser("setup")
    r = sub.add_parser("request", help="build, lint and route a request")
    r.add_argument("dataset", choices=["climate-dt", "extremes-dt"])
    r.add_argument("--param", default="167", help="paramId, e.g. 167 (2 m temperature)")
    r.add_argument("--start")
    r.add_argument("--end")
    r.add_argument("--model", default="ifs-fesom", help="ifs-fesom | ifs-nemo | icon")
    r.add_argument("--experiment", default="hist", help="hist | cont | SSP3-7.0 | Tplus2.0K")
    r.add_argument("--activity", default="baseline", help="baseline | projections | story-nudging")
    r.add_argument("--lat", type=float)
    r.add_argument("--lon", type=float)
    r.add_argument("--json", action="store_true")
    g = sub.add_parser("retrieve")
    g.add_argument("request", help="JSON request file")
    g.add_argument("-o", "--output", required=True)
    a = ap.parse_args(_negatives(sys.argv[1:] if argv is None else argv))

    if a.cmd == "setup":
        print("\n".join(setup_steps()))
        return 0
    if a.cmd == "check":
        src = token_source()
        res = {"token": src, "addresses": {"lumi": LUMI, "mn5": MN5, "leonardo": LEONARDO}}
        if not src:
            res["offer"] = (
                "No DestinE token found. Digital Twin data needs upgraded access on the "
                "DestinE Platform. Offer the user step-by-step instructions "
                "(python3 destine.py setup)."
            )
        print(
            json.dumps(res, indent=2) if a.json else "\n".join(f"{k}: {v}" for k, v in res.items())
        )
        return 0 if src else 4
    if a.cmd == "request":
        try:
            req = template(
                a.dataset, a.param, a.start, a.end, a.model, a.experiment, a.activity, a.lat, a.lon
            )
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 2
        res = {
            "collection": COLLECTION,
            "address": address_for(req),
            "request": req,
            "errors": lint(req),
            "attribution": attribution(a.dataset),
        }
        if a.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"address: {res['address']}  collection: {COLLECTION}")
            print(json.dumps(req, indent=2))
            print(f"attribution: {res['attribution']['text']}")
        return 2 if res["errors"] else 0
    req = json.loads(Path(a.request).read_text())
    errs = lint(req)
    if errs:
        print("error: " + "; ".join(errs), file=sys.stderr)
        return 2
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
    try:
        retrieve(req, a.output)
    except ImportError as e:
        print(f"error: {e}. Run with `uv run destine.py retrieve …`.", file=sys.stderr)
        return 3
    except Exception as e:  # polytope-client errors (401: re-authenticate; 403: no upgraded access)
        b = barrier_for_error(str(e))
        if b:
            return blocked(b)
        print(f"error: {str(e).splitlines()[-1] if str(e) else e!r}", file=sys.stderr)
        return 2
    print(f"saved {a.output}\nattribution: {attribution(req['dataset'])['text']}")
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
