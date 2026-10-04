#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""ECMWF Open Data catalogue helper — standard library only.

Finds the latest run, builds file/index URLs, lists steps, estimates download size from the
.index files and downloads only the selected GRIB fields with HTTP Range requests. Every result
carries freshness and the CC-BY-4.0 attribution ECMWF requires.

This is the zero-dependency path. Decoding GRIB needs earthkit (see odpoint.py / the earthkit
skill).

Examples:
  python3 odcatalog.py latest --model ifs
  python3 odcatalog.py url --date 20261001 --time 0 --step 24
  python3 odcatalog.py steps --model aifs-single --time 12
  python3 odcatalog.py estimate --param 2t,tp --step 0-48
  python3 odcatalog.py download --param 2t --step 24 -o 2t.grib2
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc
USER_AGENT = "ecmwf-weather-skills/0.1 (+https://github.com/ecmwf/ecmwf-weather-skills)"

ROOTS = {
    "ecmwf": "https://data.ecmwf.int/forecasts",
    "aws": "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    "google": "https://storage.googleapis.com/ecmwf-open-data",
}
# IFS Cycle 50r1: scda/scwv discontinued, 06/18z moved to oper/wave out to 144 h.
CUTOVER_50R1 = date(2026, 5, 13)

MODELS = {
    # model: {stream: default type}
    "ifs": {"oper": "fc", "enfo": "ef", "wave": "fc", "waef": "ef"},
    "aifs-single": {"oper": "fc", "wave": "fc"},
    "aifs-ens": {"enfo": "pf", "waef": "pf"},
}
# Approximate delay between base time and the run appearing on the server.
# Observed: a run's first files appear ~6.5 h after base time and the run completes ~1 h later.
PUBLISH_DELAY = timedelta(hours=7)
# Index requests are ~40 KB and answer in < 1 s; 30 s tolerates a slow portal.
INDEX_TIMEOUT_S = 30
# One GRIB field is <= ~1 MB; at the observed ~1-2 MB/s per connection 120 s leaves ample margin.
RANGE_TIMEOUT_S = 120
# Transient 5xx/timeouts usually clear on the 2nd try; 3 attempts with 2 s, 4 s backoff.
RANGE_RETRIES = 3
# Measured: 1 connection ~2 MB/s, 8 ~10 MB/s, 16 ~7.5 MB/s (server-side throttling) -> 8.
DEFAULT_WORKERS = 8
# data.ecmwf.int keeps ~12-14 runs (2-3 days); 72 h covers the whole window.
MAX_BACK_HOURS = 72


# --- schedule ---------------------------------------------------------------------------------


def _check(model: str, stream: str) -> None:
    if model not in MODELS:
        raise ValueError(f"unknown model {model!r}; choose from {', '.join(MODELS)}")
    if stream not in MODELS[model]:
        raise ValueError(
            f"stream {stream!r} not published for model {model!r}; choose from "
            f"{', '.join(MODELS[model])}"
        )


def stream_for(model: str, stream: str, hour: int, run_date: date | None = None) -> str:
    """Server-side stream name (pre-50r1 06/18z IFS used scda/scwv)."""
    _check(model, stream)
    run_date = run_date or datetime.now(UTC).date()
    if model == "ifs" and hour in (6, 18) and run_date < CUTOVER_50R1:
        return {"oper": "scda", "wave": "scwv"}.get(stream, stream)
    return stream


def steps_for(model: str, stream: str, hour: int, run_date: date | None = None) -> list[int]:
    _check(model, stream)
    run_date = run_date or datetime.now(UTC).date()
    if model != "ifs":
        return list(range(0, 361, 6))
    if hour in (0, 12):
        return list(range(0, 145, 3)) + list(range(150, 361, 6))
    if run_date < CUTOVER_50R1 and stream in ("oper", "wave"):
        return list(range(0, 91, 3))
    return list(range(0, 145, 3))


def default_type(model: str, stream: str) -> str:
    _check(model, stream)
    return MODELS[model][stream]


# --- URLs -------------------------------------------------------------------------------------


def file_url(
    run: datetime,
    model: str,
    stream: str,
    step: int,
    type_: str | None = None,
    source: str = "ecmwf",
    ext: str = "grib2",
    resol: str = "0p25",
) -> str:
    if source not in ROOTS:
        raise ValueError(f"unknown source {source!r}; choose from {', '.join(ROOTS)}")
    s = stream_for(model, stream, run.hour, run.date())
    t = type_ or default_type(model, stream)
    ymd, hh = run.strftime("%Y%m%d"), f"{run.hour:02d}"
    return f"{ROOTS[source]}/{ymd}/{hh}z/{model}/{resol}/{s}/{ymd}{hh}0000-{step}h-{s}-{t}.{ext}"


# --- index files ------------------------------------------------------------------------------


def parse_index(text: str) -> list[dict]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line:
            e = json.loads(line)
            e["_offset"], e["_length"] = int(e["_offset"]), int(e["_length"])
            out.append(e)
    return out


def select(
    entries: list[dict], params=None, levtype=None, levelist=None, number=None
) -> list[dict]:
    params = set(params) if params else None
    levs = {str(v) for v in levelist} if levelist else None
    nums = {str(v) for v in number} if number else None
    res = []
    for e in entries:
        if params and e.get("param") not in params:
            continue
        if levtype and e.get("levtype") != levtype:
            continue
        if levs and str(e.get("levelist")) not in levs:
            continue
        if nums and str(e.get("number")) not in nums:
            continue
        res.append(e)
    return res


def summarise_fields(entries: list[dict]) -> dict:
    by_lev: dict[str, set] = {}
    for e in entries:
        by_lev.setdefault(e["levtype"], set()).add(e["param"])

    def levels(lt):
        return sorted(
            {int(e["levelist"]) for e in entries if e["levtype"] == lt and "levelist" in e},
            reverse=(lt == "pl"),
        )

    return {
        "params": {k: sorted(v) for k, v in by_lev.items()},
        "pressure_levels": levels("pl"),
        "soil_levels": levels("sol"),
    }


def estimate_bytes(entries: list[dict]) -> int:
    return sum(e["_length"] for e in entries)


def byte_ranges(entries: list[dict]) -> list[tuple[int, int]]:
    """Inclusive byte ranges, contiguous fields merged."""
    ranges: list[list[int]] = []
    for e in sorted(entries, key=lambda e: e["_offset"]):
        start, end = e["_offset"], e["_offset"] + e["_length"] - 1
        if ranges and start == ranges[-1][1] + 1:
            ranges[-1][1] = end
        else:
            ranges.append([start, end])
    return [(a, b) for a, b in ranges]


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} B" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# --- listings ---------------------------------------------------------------------------------


def available_dates(html: str) -> list[str]:
    return sorted(set(re.findall(r'href="/forecasts/(\d{8})/"', html)))


def steps_in_listing(html: str, stream: str, type_: str) -> list[int]:
    pat = rf'-(\d+)h-{re.escape(stream)}-{re.escape(type_)}\.(?:grib2|index)"'
    return sorted({int(s) for s in re.findall(pat, html)})


# --- network ----------------------------------------------------------------------------------


def _request(
    url: str, method: str = "GET", headers: dict | None = None, timeout: int = INDEX_TIMEOUT_S
):
    h = {"User-Agent": USER_AGENT, **(headers or {})}
    return urllib.request.urlopen(
        urllib.request.Request(url, method=method, headers=h), timeout=timeout
    )


def http_exists(url: str) -> bool:
    try:
        with _request(url, method="HEAD") as r:
            return r.status == 200
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return False


def fetch_text(url: str) -> str:
    with _request(url) as r:
        return r.read().decode()


# Above 16 throughput drops (throttling) and it eats into the portal-wide 500-connection limit.
MAX_WORKERS = 16


def clamp_workers(n: int) -> int:
    return max(1, min(MAX_WORKERS, n))


def fetch_range(url: str, start: int, end: int, retries: int = RANGE_RETRIES) -> bytes:
    for attempt in range(retries):
        try:
            with _request(
                url, headers={"Range": f"bytes={start}-{end}"}, timeout=RANGE_TIMEOUT_S
            ) as r:
                if r.status != 206:
                    raise RuntimeError(f"server ignored Range request for {url}")
                return r.read()
        except (urllib.error.URLError, TimeoutError):
            if attempt == retries - 1:
                raise
            time.sleep(2 * (attempt + 1))
    raise RuntimeError("unreachable")


def download_ranges(
    jobs: list[tuple[str, int, int]], output, workers: int = DEFAULT_WORKERS, fetch=fetch_range
) -> int:
    """Download (url, start, end) byte ranges in parallel; write them in order. Returns bytes."""
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(clamp_workers(workers)) as ex, open(output, "wb") as fh:
        n = 0
        for chunk in ex.map(lambda j: fetch(*j), jobs):
            fh.write(chunk)
            n += len(chunk)
    return n


def range_jobs(per_step) -> list[tuple[str, int, int]]:
    """[(step, index_url, selected_entries)] -> [(grib_url, start, end)]."""
    return [
        (idx_url[:-5] + "grib2", a, b) for _, idx_url, sel in per_step for a, b in byte_ranges(sel)
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


def no_run_barrier(source: str) -> dict:
    return {
        "blocked": f"no published ECMWF Open Data run found on '{source}'",
        "why": "the server or the network is unavailable, or publication is delayed.",
        "user_steps": [
            "1. Check the network can reach https://data.ecmwf.int/forecasts/ "
            "(proxies and firewalls often block it).",
            "2. Retry with a mirror: --source aws or --source google.",
            "3. Service status and announcements: https://status.ecmwf.int",
        ],
        "agent": "Retry once with --source aws (then google); if all fail, report the "
        "steps above instead of using another provider's data.",
    }


def latest_run(
    model: str = "ifs",
    stream: str = "oper",
    now: datetime | None = None,
    step: int | None = None,
    exists=http_exists,
    source: str = "ecmwf",
    max_back_hours: int = MAX_BACK_HOURS,
) -> datetime | None:
    """Most recent run whose `step` (default: final step, i.e. complete run) is published."""
    now = now or datetime.now(UTC)
    t = now.replace(minute=0, second=0, microsecond=0, hour=now.hour - now.hour % 6)
    stop = now - timedelta(hours=max_back_hours)
    while t >= stop:
        s = step if step is not None else steps_for(model, stream, t.hour, t.date())[-1]
        if exists(file_url(t, model, stream, s, source=source, ext="index")):
            return t
        t -= timedelta(hours=6)
    return None


# --- freshness & attribution ------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%MZ")


def freshness(run: datetime, model: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    nxt = run + timedelta(hours=6)  # all models publish 00/06/12/18z
    return {
        "run": _iso(run),
        "age_hours": round((now - run).total_seconds() / 3600, 1),
        "next_run": _iso(nxt),
        "next_run_available": (
            f"approx {_iso(nxt + PUBLISH_DELAY)} (runs appear ~6-8 h after base time)"
        ),
    }


def attribution(year: int | None = None) -> dict:
    year = year or datetime.now(UTC).year
    return {
        "licence": "CC-BY-4.0",
        "short": f"Data: © {year} ECMWF, CC BY 4.0",
        "full": (
            "This service is based on data and products of the European Centre for Medium-Range "
            "Weather Forecasts (ECMWF). Source: www.ecmwf.int. This ECMWF data is published under "
            "a Creative Commons Attribution 4.0 International (CC BY 4.0). "
            "https://creativecommons.org/licenses/by/4.0/ ECMWF does not accept any liability "
            "whatsoever for any error or omission in the data, their availability, or for any loss "
            "or damage arising from their use."
        ),
    }


# --- CLI ----------------------------------------------------------------------------------------


def _parse_date(s: str) -> date:
    return datetime.strptime(s.replace("-", ""), "%Y%m%d").date()


def max_step(spec: str) -> int:
    return max(int(x) for x in re.split(r"[,-]", spec) if x)


def _parse_steps(spec: str, available: list[int]) -> list[int]:
    out: list[int] = []
    for part in spec.split(","):
        if "-" in part:
            a, b = (int(x) for x in part.split("-"))
            if b > available[-1]:
                raise ValueError(
                    f"step {b} beyond this run's last step {available[-1]}; "
                    "use a 00z/12z IFS run (to 360 h) or omit --date to auto-pick one"
                )
            out += [s for s in available if a <= s <= b]
        else:
            out.append(int(part))
    bad = [s for s in out if s not in available]
    if bad:
        raise ValueError(f"steps {bad} not published for this run; available: {available}")
    return out


def _resolve_run(a) -> datetime:
    if a.date:
        return datetime.combine(_parse_date(a.date), datetime.min.time(), UTC).replace(
            hour=int(a.time or 0)
        )
    # Pick the latest run that has the furthest step requested (06/18z IFS stop at 144 h).
    need = max_step(str(a.step)) if getattr(a, "step", None) is not None else None
    if need is None and a.partial:
        need = 0
    run = latest_run(a.model, a.stream, step=need, source=a.source)
    if run is None:
        raise RuntimeError("no published run found in the last 72 h")
    return run


def _emit(a, data: dict, lines: list[str]) -> None:
    if a.json:
        print(json.dumps(data, indent=2))
    else:
        print("\n".join(lines))
        if "freshness" in data:
            f = data["freshness"]
            print(
                f"Freshness  : run {f['run']} ({f['age_hours']} h old); next {f['next_run']}, "
                f"{f['next_run_available']}"
            )
        print(
            f"Attribution: {data['attribution']['short']} — full notice required in "
            "products/services (see SKILL.md)"
        )


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp, run=True):
        sp.add_argument("--model", default="ifs", help="ifs (default), aifs-single, aifs-ens")
        sp.add_argument("--stream", default=None, help="oper/enfo/wave/waef (default per model)")
        sp.add_argument("--source", default="ecmwf", choices=list(ROOTS))
        sp.add_argument("--json", action="store_true")
        if run:
            sp.add_argument("--date", help="YYYYMMDD; omit for latest complete run")
            sp.add_argument("--time", help="run hour 0/6/12/18")
            sp.add_argument(
                "--partial",
                action="store_true",
                help="with no --date: accept a run still being published",
            )

    common(sub.add_parser("latest", help="latest run"), run=False)
    sub.choices["latest"].add_argument("--partial", action="store_true")
    sp = sub.add_parser("steps", help="published steps for a run hour")
    common(sp, run=False)
    sp.add_argument("--time", default="0")
    sp.add_argument("--date")
    sp = sub.add_parser("url", help="grib2/index URL for one step")
    common(sp)
    sp.add_argument("--step", type=int, required=True)
    sp.add_argument("--type", dest="type_")
    for name in ("estimate", "download", "fields"):
        sp = sub.add_parser(name)
        common(sp)
        sp.add_argument("--step", default="0", help="e.g. 24 | 0,6,12 | 0-48")
        sp.add_argument("--type", dest="type_")
        if name != "fields":
            sp.add_argument("--param", required=True, help="comma list, e.g. 2t,tp,10u,10v")
            sp.add_argument("--levtype", help="sfc | pl | sol")
            sp.add_argument("--levelist", help="comma list of levels, e.g. 850,500")
            sp.add_argument("--number", help="ensemble members, e.g. 1,2,3")
        if name == "download":
            sp.add_argument("-o", "--output", required=True)
            sp.add_argument(
                "--workers",
                type=int,
                default=DEFAULT_WORKERS,
                help="parallel connections (default 8, max 16)",
            )

    a = p.parse_args(argv)
    try:
        a.stream = a.stream or next(iter(MODELS.get(a.model, {"oper": 0})))
        _check(a.model, a.stream)
        att = attribution()

        if a.cmd == "latest":
            run = latest_run(a.model, a.stream, step=0 if a.partial else None, source=a.source)
            if run is None:
                return blocked(no_run_barrier(a.source), a.json, code=2)
            data = {
                "model": a.model,
                "stream": a.stream,
                "run": _iso(run),
                "complete": not a.partial,
                "freshness": freshness(run, a.model),
                "attribution": att,
            }
            _emit(
                a,
                data,
                [
                    f"Latest {'(possibly partial) ' if a.partial else 'complete '}run: "
                    f"{a.model}/{a.stream} {_iso(run)}"
                ],
            )
            return 0

        if a.cmd == "steps":
            d = _parse_date(a.date) if a.date else None
            steps = steps_for(a.model, a.stream, int(a.time), d)
            data = {
                "model": a.model,
                "stream": a.stream,
                "time": int(a.time),
                "steps": steps,
                "attribution": att,
            }
            _emit(
                a,
                data,
                [
                    f"{a.model}/{a.stream} {int(a.time):02d}z steps: "
                    f"{steps[0]}..{steps[-1]} ({len(steps)} steps)"
                ],
            )
            return 0

        run = _resolve_run(a)
        available = steps_for(a.model, a.stream, run.hour, run.date())

        if a.cmd == "url":
            _parse_steps(str(a.step), available)
            g = file_url(run, a.model, a.stream, a.step, a.type_, a.source)
            data = {
                "run": _iso(run),
                "grib2": g,
                "index": g[:-5] + "index",
                "attribution": att,
            }
            _emit(a, data, [f"GRIB2: {g}", f"Index: {data['index']}"])
            return 0

        steps = _parse_steps(a.step, available)
        per_step = []
        for s in steps:
            idx_url = file_url(run, a.model, a.stream, s, a.type_, a.source, ext="index")
            entries = parse_index(fetch_text(idx_url))
            if a.cmd == "fields":
                per_step.append((s, idx_url, entries))
                continue
            sel = select(
                entries,
                a.param.split(","),
                a.levtype,
                a.levelist.split(",") if a.levelist else None,
                a.number.split(",") if a.number else None,
            )
            if not sel:
                have = sorted({e["param"] for e in entries})
                raise ValueError(f"no fields match at step {s}; params in file: {', '.join(have)}")
            per_step.append((s, idx_url, sel))

        if a.cmd == "fields":
            s, idx_url, entries = per_step[0]
            summ = summarise_fields(entries)
            data = {
                "run": _iso(run),
                "step": s,
                **summ,
                "freshness": freshness(run, a.model),
                "attribution": att,
            }
            lines = [f"Fields in {a.model}/{a.stream} {_iso(run)} step {s}:"]
            lines += [f"  {k}: {', '.join(v)}" for k, v in summ["params"].items()]
            if summ["pressure_levels"]:
                lines.append(
                    f"  pressure levels (hPa): {', '.join(map(str, summ['pressure_levels']))}"
                )
            if summ["soil_levels"]:
                lines.append(f"  soil layers: {', '.join(map(str, summ['soil_levels']))}")
            _emit(a, data, lines)
            return 0

        total = sum(estimate_bytes(sel) for _, _, sel in per_step)
        nfields = sum(len(sel) for _, _, sel in per_step)
        data = {
            "run": _iso(run),
            "model": a.model,
            "stream": a.stream,
            "steps": steps,
            "fields": nfields,
            "bytes": total,
            "size": human_size(total),
            "freshness": freshness(run, a.model),
            "attribution": att,
        }

        if a.cmd == "estimate":
            _emit(
                a,
                data,
                [
                    f"Request : {a.model}/{a.stream} {_iso(run)} steps {a.step} params {a.param}",
                    f"Estimate: {nfields} fields, {human_size(total)} "
                    "(Range download of selected fields only)",
                ],
            )
            return 0

        # download
        download_ranges(range_jobs(per_step), a.output, workers=a.workers)
        data["output"] = a.output
        _emit(
            a,
            data,
            [
                f"Downloaded {nfields} fields ({human_size(total)}) -> {a.output}",
                "Decode with earthkit-data (see the earthkit skill).",
            ],
        )
        return 0
    except (ValueError, RuntimeError, urllib.error.URLError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
