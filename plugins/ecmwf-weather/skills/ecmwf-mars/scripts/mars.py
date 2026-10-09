#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

# /// script
# requires-python = ">=3.10"
# dependencies = ["earthkit-data[mars]>=1.2"]
# ///
"""MARS archive helper — lint, estimate, plan, cost and retrieve MARS requests.

lint/estimate/plan are standard library and offline. cost/retrieve go through the ECMWF Web API
(api.ecmwf.int, credentials in ~/.ecmwfapirc or ECMWF_API_*) or a local `mars` client on ECMWF
systems; `uv run` installs earthkit-data[mars] (ecmwf-api-client). Requests can be JSON or MARS
text ("retrieve, class=od, ...").

  python3 mars.py check                          # credentials / local client (names only)
  python3 mars.py lint request.json              # errors, warnings, size estimate, MARS text
  python3 mars.py plan request.json              # split into tape-friendly monthly chunks
  uv run  mars.py cost request.json              # server-side size, tape vs disk (minutes: queued)
  uv run  mars.py retrieve request.json -o out.grib
"""

from __future__ import annotations

import argparse
import calendar
import contextlib
import importlib
import json
import os
import re
import shutil
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

# Never write __pycache__ into the installed skill (it may be read-only, and skills must
# not be modified); sibling scripts are imported below.
sys.dont_write_bytecode = True
ecmwf_status = importlib.import_module(
    "ecmwf_status"
)  # sibling script: what ECMWF says about service status

REQUIRED = ("class", "stream", "type", "levtype", "param", "date", "time")
FORECAST_TYPES = {"fc", "pf", "cf", "em", "es", "ep", "fcmean", "fcmax", "fcmin"}
# MARS retrieve keywords accepted by lint. Anything else is almost always a mistake carried
# over from CDS or another API (dataset=, format=, level=, variable=, product_type=).
KNOWN = set(REQUIRED) | {
    "expver",
    "levelist",
    "step",
    "number",
    "grid",
    "area",
    "target",
    "domain",
    "origin",
    "hdate",
    "fcmonth",
    "fcperiod",
    "method",
    "system",
    "anoffset",
    "frequency",
    "direction",
    "resol",
    "accuracy",
    "packing",
    "interpolation",
    "rotation",
    "frame",
    "repres",
    "use",
    "database",
    "fieldset",
    "obstype",
    "reportype",
    "range",
    "quantile",
    "channel",
    "ident",
    "instrument",
    "diagnostic",
    "iteration",
    "refdate",
    "product",
    "section",
    "padding",
    "truncation",
    "intgrid",
    "gaussian",
    "bitmap",
}
# Common wrong keywords and what MARS calls them.
SUGGEST = {
    "level": "levelist",
    "levels": "levelist",
    "variable": "param",
    "parameter": "param",
    "dataset": "class (and stream/type)",
    "product_type": "type",
    "format": "nothing — MARS returns GRIB; set grid/area for interpolation",
    "data_format": "nothing — MARS returns GRIB",
    "year": "date",
    "month": "date",
    "day": "date",
    "steps": "step",
}
# Number of levels when levelist=all (approximate; depends on class and cycle).
ALL_LEVELS = {("ea", "pl"): 37, ("ea", "ml"): 137, ("od", "pl"): 25, ("od", "ml"): 137}
# Native grid points when no `grid` is given: IFS HRES O1280, ERA5 N320 (reduced Gaussian).
NATIVE_POINTS = {"od": 6_599_680, "ea": 542_080}
# GRIB2 with 16-bit packing ≈ 2 bytes per point (CCSDS compression is often ~2x smaller).
BYTES_PER_POINT = 2
# One retrieval is capped at 75 GB server-side (MARS client log); flag large ones early.
WARN_BYTES = 20 * 1024**3
CAP_BYTES = 75 * 1024**3
ERA5_CLASSES = {"ea", "e5", "ep"}
# Copernicus regional reanalyses, class=rr, told apart by `origin`: name, native points
# (decoded from the archived GRIB) and grid type, dataset DOI.
REGIONAL = {
    "no-ar-ce": ("CARRA-East", 789 * 989, "Lambert conformal 2.5 km", "10.24381/cds.713858f6"),
    "no-ar-cw": ("CARRA-West", 1069 * 1269, "Lambert conformal 2.5 km", "10.24381/cds.713858f6"),
    "no-ar-pa": ("pan-CARRA", 2869 * 2869, "polar stereographic 2.5 km", "10.24381/f5effe24"),
    "se-al-ec": ("CERRA", 1069 * 1069, "Lambert conformal 5.5 km", "10.24381/cds.622a565a"),
    "fr-ms-ec": ("CERRA-Land", 1069 * 1069, "Lambert conformal 5.5 km", "10.24381/cds.a7f3cd0b"),
}
# Archives most requests target; other classes exist (reanalyses, projects) but a mistaken
# class silently retrieves the wrong dataset, so lint asks the user to confirm them.
COMMON_CLASSES = {"od", "ea", "ai", "rd", "e5", "ep", "rr", "ei", "mc", "ce", "s2", "ti"}


# --- parse / format -------------------------------------------------------------------------------


def parse_request(text: str) -> dict:
    t = text.strip()
    if t.startswith("{"):
        return {
            k.lower(): str(v) if not isinstance(v, list) else "/".join(map(str, v))
            for k, v in json.loads(t).items()
        }
    parts = [p.strip() for p in t.replace("\n", " ").split(",") if p.strip()]
    if parts and "=" not in parts[0]:
        parts = parts[1:]  # verb
    out = {}
    for p in parts:
        k, _, v = p.partition("=")
        out[k.strip().lower()] = v.strip()
    return out


def format_request(req: dict, verb: str = "retrieve") -> str:
    return verb + ",\n" + ",\n".join(f"    {k}={v}" for k, v in req.items())


def _parse_day(s: str) -> date | None:
    for fmt in ("%Y-%m-%d", "%Y%m%d"):
        with contextlib.suppress(ValueError):
            return datetime.strptime(s, fmt).date()
    return None


def expand(value: str) -> list[str]:
    """Expand MARS list syntax: a/b/c, a/to/b, a/to/b/by/n (dates or numbers)."""
    parts = str(value).split("/")
    low = [p.lower() for p in parts]
    if "to" in low:
        i = low.index("to")
        a, b = parts[i - 1], parts[i + 1]
        step = int(parts[low.index("by") + 1]) if "by" in low else 1
        da, db = _parse_day(a), _parse_day(b)
        if da and db:
            fmt = "%Y-%m-%d" if "-" in a else "%Y%m%d"
            out, d = [], da
            while d <= db:
                out.append(d.strftime(fmt))
                d += timedelta(days=step)
            return parts[: i - 1] + out
        return parts[: i - 1] + [str(x) for x in range(int(a), int(b) + 1, step)]
    return parts


def _count(req: dict, key: str) -> int:
    v = req.get(key)
    if v is None:
        return 1
    if str(v).lower() == "all" and key == "levelist":
        return ALL_LEVELS.get((req.get("class"), req.get("levtype")), 25)
    return len(expand(v))


# --- lint / estimate / plan -----------------------------------------------------------------------


def _two_numbers(v: str) -> list[float] | None:
    try:
        nums = [float(x) for x in str(v).split("/")]
    except ValueError:
        return None
    return nums


def estimate(req: dict) -> dict:
    fields = 1
    for k in ("param", "date", "time", "levelist", "number"):
        fields *= _count(req, k)
    if req.get("type") in FORECAST_TYPES or "step" in req:
        fields *= _count(req, "step")
    grid, area = (
        _two_numbers(req.get("grid", "")) if req.get("grid") else None,
        _two_numbers(req.get("area", "")) if req.get("area") else None,
    )
    if grid and len(grid) == 2:
        if area and len(area) == 4:
            n, w, s, e = area
            pts = (round((n - s) / grid[1]) + 1) * (round((e - w) / grid[0]) + 1)
        else:
            pts = (round(180 / grid[1]) + 1) * round(360 / grid[0])
    else:
        regional = REGIONAL.get(req.get("origin", "")) if req.get("class") == "rr" else None
        pts = regional[1] if regional else NATIVE_POINTS.get(req.get("class"), NATIVE_POINTS["od"])
    b = fields * pts * BYTES_PER_POINT
    return {
        "fields": fields,
        "points_per_field": pts,
        "bytes": b,
        "size": _human(b),
        "note": "rough estimate; `mars.py cost` asks the server (exact size, tape vs disk)",
    }


def _human(n: float) -> str:
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} TB"


def _months(req: dict) -> int:
    days = [d for d in (_parse_day(x) for x in expand(req.get("date", ""))) if d]
    return len({(d.year, d.month) for d in days})


def lint(req: dict) -> dict:
    errs, warns = [], []
    for k in REQUIRED:
        if k not in req:
            errs.append(f"missing required keyword '{k}'")
    for k in req:
        if k not in KNOWN:
            hint = f" — use {SUGGEST[k]}" if k in SUGGEST else ""
            errs.append(f"unknown MARS keyword '{k}'{hint}")
    if req.get("levtype") in ("pl", "ml", "pt", "pv") and "levelist" not in req:
        errs.append(f"levtype={req.get('levtype')} needs 'levelist' (e.g. 1000/850/500 or all)")
    if req.get("type") == "pf" and "number" not in req:
        errs.append("type=pf (ensemble perturbed members) needs 'number' (e.g. 1/to/50)")
    if req.get("type") in FORECAST_TYPES and "step" not in req:
        errs.append(f"type={req.get('type')} is a forecast: add 'step' (e.g. 0/to/72/by/6)")
    if req.get("type") == "an" and req.get("step") not in (None, "0"):
        warns.append("type=an (analysis) has no forecast steps — drop 'step' or use type=fc")
    if "grid" in req:
        g = _two_numbers(req["grid"])
        if not g or len(g) != 2:
            errs.append("grid must be 'dlon/dlat' in degrees, e.g. grid=0.25/0.25")
    if "area" in req:
        a = _two_numbers(req["area"])
        if not a or len(a) != 4:
            errs.append("area must be north/west/south/east, e.g. 72/-25/30/45")
        elif a[0] < a[2]:
            errs.append(f"area north {a[0]} is below south {a[2]} — order is north/west/south/east")
    if req.get("class") == "rr":
        reg = REGIONAL.get(req.get("origin", ""))
        if reg is None:
            names = ", ".join(f"{o} ({v[0]})" for o, v in REGIONAL.items())
            errs.append(f"class=rr (regional reanalyses) needs origin: {names}")
        elif "area" in req and "grid" not in req:
            errs.append(
                f"{reg[0]} is on a {reg[2]} grid, which MARS cannot crop: area alone fails "
                "('croppedRepresentation() not implemented') — add grid, e.g. grid=0.05/0.05, "
                "to regrid to regular lat/lon and crop"
            )
    dates = str(req.get("date", "")).split("/")
    if len(dates) == 2 and all(_parse_day(d) for d in dates) and dates[0] != dates[1]:
        warns.append(
            f"date={req['date']} lists only these two days — for the whole period use "
            f"date={dates[0]}/to/{dates[1]}"
        )
    if req.get("levtype") == "pl" and "levelist" in req and str(req["levelist"]).lower() != "all":
        try:
            levels = [float(x) for x in expand(req["levelist"])]
        except ValueError:
            levels = []
        if levels and len(levels) > ALL_LEVELS[("ea", "pl")]:
            errs.append(
                f"levtype=pl with {len(levels)} levels — pressure levels are hPa values "
                "(1000/850/500 or levelist=all); 1/to/137 are model levels: use levtype=ml"
            )
    if req.get("class") in ("ti", "s2"):
        errs.append(
            f"class={req['class']} (TIGGE/S2S) is no longer served through the Web API: use the "
            "ECMWF Data Store, https://ecds.ecmwf.int (same key as CDS; accept the TIGGE/S2S "
            "licence on the dataset page first)"
        )
    if req.get("class") not in COMMON_CLASSES:
        warns.append(
            f"class={req.get('class')} is not a common archive — operational IFS (HRES/ENS) is "
            "class=od, ERA5 class=ea, AIFS class=ai, research experiments class=rd; check it"
        )
    if "expver" not in req:
        warns.append("no 'expver' — defaults to 1 (operational/final data)")
    if _months(req) > 1:
        warns.append(
            f"request spans {_months(req)} months — archived data is stored on tape by month; "
            "submit one request per month (`mars.py plan`) with all params/levels/times of that "
            "month, looping months in date order"
        )
    e = estimate(req)
    if e["bytes"] > CAP_BYTES:
        warns.append(
            f"estimated {e['size']} — above the 75 GB per-retrieval cap: split the request "
            "(fewer parameters or levels per request, still one month each)"
        )
    elif e["bytes"] > WARN_BYTES:
        warns.append(
            f"estimated {e['size']} — large but under the 75 GB cap; reduce with a coarser "
            "grid or an area if the full domain isn't needed"
        )
    if not req.get("grid") and req.get("class") == "od":
        warns.append(
            "no 'grid' — native O1280 (~9 km) fields are large; "
            "add grid=0.25/0.25 and an area if possible"
        )
    return {"errors": errs, "warnings": warns, "estimate": e}


def plan(req: dict) -> list[dict]:
    days = [d for d in (_parse_day(x) for x in expand(req.get("date", ""))) if d]
    if len({(d.year, d.month) for d in days}) <= 1:
        return [dict(req)]
    fmt = "%Y-%m-%d" if "-" in req["date"] else "%Y%m%d"
    chunks, seen = [], []
    for d in days:
        if (d.year, d.month) not in seen:
            seen.append((d.year, d.month))
    for y, m in seen:
        month_days = [d for d in days if (d.year, d.month) == (y, m)]
        first, last = month_days[0], month_days[-1]
        full = (
            first.day == 1
            and last.day == calendar.monthrange(y, m)[1]
            and len(month_days) == last.day
        )
        date_val = (
            f"{first.strftime(fmt)}/to/{last.strftime(fmt)}"
            if full or len(month_days) > 1
            else first.strftime(fmt)
        )
        chunks.append({**req, "date": date_val})
    return chunks


def parse_cost(text: str) -> dict:
    return {k.strip(): int(v) for k, v in re.findall(r"(\w+)=(\d+);", text)}


# --- access ---------------------------------------------------------------------------------------


def credentials(env=os.environ, home: Path | None = None, which=shutil.which) -> dict:
    home = home or Path.home()
    if all(env.get(k) for k in ("ECMWF_API_KEY", "ECMWF_API_URL", "ECMWF_API_EMAIL")):
        web = "ECMWF_API_* environment"
    elif env.get("ECMWF_API_RC_FILE") and Path(env["ECMWF_API_RC_FILE"]).exists():
        web = "ECMWF_API_RC_FILE"
    elif (home / ".ecmwfapirc").exists():
        web = "~/.ecmwfapirc"
    else:
        web = None
    client = env.get("MARS_CLIENT_COMMAND") or which("mars")
    return {"webapi": web, "mars_client": client}


def _webapi_settings(env, home: Path) -> dict | None:
    if all(env.get(k) for k in ("ECMWF_API_KEY", "ECMWF_API_URL", "ECMWF_API_EMAIL")):
        return {
            "url": env["ECMWF_API_URL"],
            "key": env["ECMWF_API_KEY"],
            "email": env["ECMWF_API_EMAIL"],
        }
    rc = Path(env["ECMWF_API_RC_FILE"]) if env.get("ECMWF_API_RC_FILE") else home / ".ecmwfapirc"
    return json.loads(rc.read_text()) if rc.exists() else None


def _http_json(url: str, headers: dict) -> tuple[int, dict]:
    import urllib.error
    import urllib.request

    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, {}


def verify_webapi(env=os.environ, home: Path | None = None, fetch=_http_json) -> dict:
    """Seconds-fast key check (Web API who-am-i). Reports the account id, never the key.

    A valid key proves the machine can use the Web API; MARS rights for a given class/stream
    still depend on the account and only `mars.py cost` on a real request proves those.
    """
    cfg = _webapi_settings(env, home or Path.home())
    if not cfg or fetch is None:
        return {"verified": None}
    headers = {"X-ECMWF-KEY": cfg["key"], "From": cfg["email"], "Accept": "application/json"}
    try:
        status, body = fetch(cfg["url"].rstrip("/") + "/who-am-i", headers)
    except OSError as e:
        return {"verified": False, "error": f"Web API unreachable: {e}"}
    if status == 200:
        return {"verified": True, "account": body.get("uid")}
    return {"verified": False, "error": f"Web API rejected the key (HTTP {status})"}


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
    """Map a Web API / MARS error to a barrier with instructions (None if not an access one)."""
    if ecmwf_status.NETWORK_ERROR.search(message):
        return ecmwf_status.network_barrier("webapi", message.splitlines()[-1][:200])
    if re.search(r"(?i)invalid (api )?key|key (has )?expired|\b401\b|authenti", message):
        return {
            "blocked": "the ECMWF Web API rejected the key",
            "why": "the key is mistyped, from another account, or expired (keys last a year).",
            "user_steps": [*setup_steps()[:3], "4. Re-run: python3 mars.py check"],
            "agent": "Give these steps; do not retry until the user has a fresh key.",
        }
    if re.search(
        r"(?i)no access|not authori[sz]ed|permission|forbidden|\b403\b|restricted", message
    ):
        return {
            "blocked": "this account has no MARS rights for the requested data",
            "why": "MARS data is licensed; a valid key alone does not grant archive access.",
            "user_steps": [setup_steps()[3], "5. Then re-run the request."],
            "agent": "Explain the access route; offer free alternatives (open-data for recent "
            "forecasts, cds-ads for ERA5, ECDS for TIGGE/S2S) and do not retry.",
        }
    return None


def setup_steps() -> list[str]:
    """Exact steps to obtain the ECMWF Web API key and MARS access (verified 2026-10)."""
    return [
        "1. Create an ECMWF account: open https://api.ecmwf.int/v1/key/, click Login, then "
        "'Register new user'.",
        "2. Log in and open https://api.ecmwf.int/v1/key/ — it shows your url, key and email. "
        "The key expires after one year (ECMWF emails you before it does).",
        "3. Save it as ~/.ecmwfapirc and run: chmod 600 ~/.ecmwfapirc\n"
        '   {"url": "https://api.ecmwf.int/v1", "key": "<key>", "email": "<email>"}\n'
        "   (or set ECMWF_API_URL, ECMWF_API_KEY and ECMWF_API_EMAIL together)",
        "4. The key alone gives no MARS data: users at Member and Co-operating State national "
        "weather services get access through their Computing Representative "
        "(https://www.ecmwf.int/en/about/contact-us/computing-representatives); everyone else "
        "needs a service agreement "
        "(https://www.ecmwf.int/en/forecasts/accessing-forecasts/service-agreements). "
        "Questions: https://support.ecmwf.int",
        "5. Check: python3 mars.py check",
        "Free alternatives: ERA5 via the ecmwf-cds-ads skill; TIGGE and S2S via the ECMWF "
        "Data Store "
        "(https://ecds.ecmwf.int); recent forecasts via the ecmwf-open-data skill.",
    ]


def licence_note(req: dict) -> str:
    if req.get("class") == "rr" and req.get("origin") in REGIONAL:
        name, _, _, doi = REGIONAL[req["origin"]]
        return (
            f"{name} (Copernicus Climate Change Service regional reanalysis) — CC BY 4.0. "
            "Credit: 'Generated using Copernicus Climate Change Service information <year>' "
            f"and cite the dataset DOI {doi}."
        )
    if req.get("class") in ERA5_CLASSES:
        return (
            "ERA5 (Copernicus Climate Change Service) — CC BY 4.0. "
            "Credit: 'Generated using Copernicus Climate Change Service information <year>' "
            "and cite the ERA5 DOI 10.24381/cds.adbb2d47."
        )
    return (
        "Licensed ECMWF data — use under your organisation's ECMWF licence; "
        "credit '© ECMWF' and check the licence before redistributing or publishing."
    )


# --- server (Web API / local client) --------------------------------------------------------------


def server_cost(req: dict) -> dict:
    from ecmwfapi import ECMWFService

    out = Path(".mars-cost.txt")
    text = "list,\n" + ",\n".join(
        f"{k}={v}"
        for k, v in {**req, "output": "cost"}.items()
        if k not in ("grid", "area", "target")
    )
    with contextlib.redirect_stdout(sys.stderr):  # ecmwf-api-client logs to stdout
        ECMWFService("mars", quiet=True).execute(text, str(out))
    try:
        return parse_cost(out.read_text())
    finally:
        out.unlink(missing_ok=True)


def absolute_dates(req: dict, today: date | None = None) -> dict:
    """Relative dates (-1 = yesterday) as YYYY-MM-DD: MARS accepts them, earthkit-data doesn't."""
    today = today or date.today()

    def conv(v: str) -> str:
        return (today - timedelta(days=-int(v))).isoformat() if re.fullmatch(r"-\d+", v) else v

    if "date" not in req:
        return dict(req)
    return {**req, "date": "/".join(conv(p) for p in str(req["date"]).split("/"))}


def retrieve(req: dict, output: str) -> None:
    import earthkit.data as ekd

    req = absolute_dates({k: v for k, v in req.items() if k != "target"})
    with contextlib.redirect_stdout(sys.stderr):
        src = ekd.from_source("mars", req)
    shutil.copy(src.path, output)


# --- CLI ------------------------------------------------------------------------------------------


def _load(arg: str) -> dict:
    p = Path(arg)
    return parse_request(p.read_text() if p.exists() else arg)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("setup", help="how to obtain the Web API key and MARS access")
    sp = sub.add_parser("check")
    sp.add_argument("--json", action="store_true")
    for name in ("lint", "estimate", "plan", "cost", "retrieve"):
        sp = sub.add_parser(name)
        sp.add_argument("request", help="JSON or MARS-text file, or an inline MARS request string")
        sp.add_argument("--json", action="store_true")
        if name == "retrieve":
            sp.add_argument("-o", "--output", required=True)
    a = ap.parse_args(argv)

    if a.cmd == "setup":
        print("\n".join(setup_steps()))
        return 0

    if a.cmd == "check":
        c = credentials()
        if c["webapi"]:
            c.update(verify_webapi())
        c["how"] = (
            "Web API key: https://api.ecmwf.int/v1/key/ -> ~/.ecmwfapirc. A verified key means "
            "the Web API accepts you; MARS rights per dataset depend on the account "
            "(Member/Co-operating State or licensed users) — `uv run mars.py cost` on a tiny "
            "request proves them, but queues for minutes."
        )
        if not (c["webapi"] or c["mars_client"]) or c.get("verified") is False:
            c["offer"] = (
                "Say once what MARS access would add and offer step-by-step setup "
                "instructions (python3 mars.py setup); give them only if accepted."
            )
        print(json.dumps(c, indent=2) if a.json else "\n".join(f"{k}: {v}" for k, v in c.items()))
        if c.get("verified") is False:
            return 4
        return 0 if (c["webapi"] or c["mars_client"]) else 4

    try:
        req = _load(a.request)
        if a.cmd in ("lint", "estimate"):
            r = lint(req)
            r["mars"] = format_request(req)
            r["licence"] = licence_note(req)
            r["chunks"] = len(plan(req))
            if a.json:
                print(json.dumps(r, indent=2))
            else:
                print(r["mars"])
                for e in r["errors"]:
                    print(f"ERROR: {e}")
                for w in r["warnings"]:
                    print(f"warning: {w}")
                e = r["estimate"]
                print(
                    f"estimate: {e['fields']} fields x {e['points_per_field']} points "
                    f"≈ {e['size']} ({e['note']})"
                )
                print(f"licence: {r['licence']}")
            return 2 if r["errors"] else 0
        if a.cmd == "plan":
            chunks = plan(req)
            print(
                json.dumps(chunks, indent=2)
                if a.json
                else "\n\n".join(format_request(c) for c in chunks)
                + f"\n\n{len(chunks)} request(s); "
                "submit in this order (date order is tape order)."
            )
            return 0
        if not any(credentials().values()):
            return blocked(
                {
                    "blocked": "no MARS access configured on this machine",
                    "why": "MARS needs an ECMWF Web API key (or a local mars client) and "
                    "an account with archive rights.",
                    "user_steps": setup_steps(),
                    "agent": "Offer these steps; meanwhile lint, estimate and plan work "
                    "offline, and suggest free alternatives.",
                },
                a.json,
            )
        errs = lint(req)["errors"]
        if errs:
            print("error: fix the request first:\n  " + "\n  ".join(errs), file=sys.stderr)
            return 2
        if a.cmd == "cost":
            c = server_cost(req)
            c["size_human"] = _human(c.get("size", 0))
            c["on_tape"] = c.get("number_of_offline_fields", 0) > 0
            print(
                json.dumps(c, indent=2) if a.json else "\n".join(f"{k}: {v}" for k, v in c.items())
            )
            return 0
        retrieve(req, a.output)
        print(f"saved {a.output}\nlicence: {licence_note(req)}")
        return 0
    except ImportError as e:
        print(f"error: {e}. Run with `uv run mars.py …`.", file=sys.stderr)
        return 3
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except Exception as e:  # ecmwf-api-client / MARS errors
        b = barrier_for_error(str(e))
        if b:
            return blocked(b, a.json)
        print(f"error: {str(e).splitlines()[-1] if str(e) else repr(e)}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
