# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""No credential-shaped values may be committed (AGENTS.md: never place credentials in the repo)."""

import re
import subprocess

from conftest import ROOT

PATTERNS = {
    "JWT / OAuth token": re.compile(
        r"\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"
    ),
    "key followed by a 32-hex value": re.compile(
        r"(?i)(key|token|password|secret)[\"']?\s*[:=]\s*[\"']?[0-9a-f]{32}\b"
    ),
    "key followed by a UUID": re.compile(
        r"(?i)(key|token)[\"']?\s*[:=]\s*[\"']?[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    ),
    "private key block": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
}


def test_no_credentials_in_tracked_files():
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout.split()
    hits = []
    for f in files:
        path = ROOT / f
        if not path.is_file() or path.suffix in (".png", ".grib2", ".pyc"):
            continue
        text = path.read_text(errors="ignore")
        hits += [f"{f}: {name}" for name, rx in PATTERNS.items() if rx.search(text)]
    assert not hits, "credential-like values committed:\n" + "\n".join(hits)


def test_patterns_detect_synthetic_credentials():
    hexkey = "ab" * 16
    uuid = "-".join(["a" * 8, "b" * 4, "c" * 4, "d" * 4, "e" * 12])
    samples = [
        f'"key": "{hexkey}"',
        f"key: {uuid}",
        "eyJ" + "a" * 30 + "." + "b" * 30 + "." + "c" * 20,
    ]
    for s in samples:
        assert any(rx.search(s) for rx in PATTERNS.values()), s
