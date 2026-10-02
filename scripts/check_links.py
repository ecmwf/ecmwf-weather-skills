#!/usr/bin/env python3
"""Probe every URL mentioned in the skills (SKILL.md, references, scripts). Weekly in CI.

  python3 scripts/check_links.py          # exit 1 if any URL is dead

URL templates (containing {…}) and truncated examples (…) are skipped. Third-party outages
should not block pull requests, so this runs on a schedule, not on every change.
"""

from __future__ import annotations

import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "ecmwf-weather" / "skills"
URL = re.compile(r"https?://[^\s<>\"'`)\]|\\]+")
# Known non-browsable URLs — each with the reason it's expected to fail a plain GET.
IGNORE = {
    "https://data.ecmwf.int/forecasts": "API root used as a path prefix; only full paths resolve",
    "https://charts.ecmwf.int/opencharts-api/v1": "API root used as a path prefix",
    "https://ai4edataeuwest.blob.core.windows.net/ecmwf": "Azure mirror needs a SAS token (documented)",
    "https://github.com/ecmwf/ecmwf-weather-skill": "this repository — 404 until published",
}
IGNORE_HOSTS = ("http://127.0.0.1", "http://localhost")
# Hosts that need credentials or only accept API calls; reachability (any HTTP answer) is enough.
AUTH_OK = {401, 403, 405}
TIMEOUT_S = 30


def extract_urls(text: str) -> list[str]:
    out = []
    for u in URL.findall(text):
        u = u.rstrip(".,;:")
        if (
            "{" in u
            or "}" in u
            or "…" in u
            or "<" in u
            or u in IGNORE
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
    req = urllib.request.Request(
        url, headers={"User-Agent": "ecmwf-weather-skill-linkcheck/0.1"}
    )
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
    bad = [
        (u, s)
        for u, s in results
        if not (isinstance(s, int) and (s < 400 or s in AUTH_OK))
    ]
    for u, s in bad:
        print(f"DEAD {s}: {u}")
    print(f"{len(urls)} URLs, {len(bad)} dead")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
