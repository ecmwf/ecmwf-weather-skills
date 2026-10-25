#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Probe every URL mentioned in the skills (SKILL.md, references, scripts). Weekly in CI.

  python3 scripts/check_links.py          # exit 1 if any URL is dead

URL templates (containing {…}), truncated examples (… or ...) and URLs cut short by a
placeholder (`https://host/<name>/…`) are skipped; examples in the skills name real
repositories instead. Service endpoints (the MCP backends, the NOAA API base) are left to
`check_endpoints.py`, which probes them with the request the scripts make. Third-party outages
should not block pull requests, so this runs on a schedule, not on every change.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import check_endpoints

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "ecmwf-weather" / "skills"
URL = re.compile(r"https?://[^\s<>\"'`)\]|\\]+")
# Known non-browsable URLs — each with the reason it's expected to fail a plain GET.
IGNORE = {
    "https://data.ecmwf.int/forecasts": "API root used as a path prefix; only full paths resolve",
    "https://charts.ecmwf.int/opencharts-api/v1": "API root used as a path prefix",
    "https://ai4edataeuwest.blob.core.windows.net/ecmwf": (
        "Azure mirror needs a SAS token (documented)"
    ),
}
# API endpoints the scripts call: a plain GET answers 406 (MCP) or 404 (API base), so each is
# checked by check_endpoints.py with a real request instead.
SERVICE_ENDPOINTS = {
    url: "service endpoint, probed by check_endpoints" for url in check_endpoints.SERVICE_ENDPOINTS
}
IGNORE_HOSTS = ("http://127.0.0.1", "http://localhost")
# Hosts that need credentials or only accept API calls; reachability (any HTTP answer) is enough.
AUTH_OK = {401, 403, 405}
TIMEOUT_S = 30


def extract_urls(text: str) -> list[str]:
    out = []
    for m in URL.finditer(text):
        raw = m.group()
        u = raw.rstrip(".,;:")
        if (
            "{" in u
            or "}" in u
            or "…" in raw
            or "..." in raw  # judged before the strip: `…?body=...` is a truncated example
            or text[m.end() : m.end() + 1] == "<"  # the pattern stops at a placeholder
            or "<" in u
            or u in IGNORE
            or u in SERVICE_ENDPOINTS
            or u.startswith(IGNORE_HOSTS)
        ):
            continue
        out.append(u)
    return out


def collect(skills: Path = SKILLS) -> list[str]:
    urls: set[str] = set()
    for f in skills.rglob("*"):
        if f.is_file() and f.suffix in (".md", ".py", ".js", ".html", ".json"):
            urls.update(extract_urls(f.read_text(errors="replace")))
    return sorted(urls)


def probe(url: str) -> tuple[str, int | str]:
    req = urllib.request.Request(url, headers={"User-Agent": "ecmwf-weather-skills-linkcheck/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as r:
            return url, r.status
    except urllib.error.HTTPError as e:
        return url, e.code
    except Exception as e:  # DNS, TLS, timeout
        return url, type(e).__name__


def main() -> int:
    urls = collect()
    with ThreadPoolExecutor(8) as ex:
        results = list(ex.map(probe, urls))
    bad = [(u, s) for u, s in results if not (isinstance(s, int) and (s < 400 or s in AUTH_OK))]
    for u, s in bad:
        print(f"DEAD {s}: {u}")
    print(f"{len(urls)} URLs, {len(bad)} dead")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
