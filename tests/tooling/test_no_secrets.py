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
    # A Jira personal access token is about 44 base64 characters with no recognisable prefix.
    "Jira personal access token": re.compile(
        r"\bJIRA_PAT[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9+/_=-]{20,}"
    ),
    "bearer token": re.compile(
        r"(?i)\bAuthorization[\"']?\s*:\s*[\"']?Bearer\s+[A-Za-z0-9+/_=.-]{32,}"
    ),
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
    pat = "Zm9vYmFyYmF6cXV4c3ludGhldGljdG9rZW4xMjM0"  # synthetic, 40 base64 characters
    samples = [
        f'"key": "{hexkey}"',
        f"key: {uuid}",
        "eyJ" + "a" * 30 + "." + "b" * 30 + "." + "c" * 20,
        f"JIRA_PAT={pat}",
        f'JIRA_PAT = "{pat}"',
        f"export JIRA_PAT='{pat}'",
        f"Authorization: Bearer {pat}",
        f'headers = {{"Authorization": "Bearer {pat}"}}',
    ]
    for s in samples:
        assert any(rx.search(s) for rx in PATTERNS.values()), s


def test_jira_token_patterns_leave_names_and_placeholders_alone():
    placeholders = [
        "JIRA_PAT=<your personal access token>",
        "JIRA_PAT is not set",
        "the variable JIRA_PAT holds it",
        '"JIRA_PAT" if token else None',
        "JIRA_PAT=",
        "Authorization: Bearer <token>",
        'f"Bearer {token}"',
        'headers["Authorization"] = f"Bearer {token}"',
        "Authorization: Bearer …",
    ]
    for s in placeholders:
        assert not any(rx.search(s) for rx in PATTERNS.values()), s
