# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0
"""Every access check survives every broken credentials file: a BLOCKED report (exit 4) that
names the file and the keys it found — never a traceback, never a value."""

import json
import subprocess
import sys

import pytest
from conftest import SKILLS

FAKE_KEY, FAKE_EMAIL, FAKE_URL = "zz-fake-secret-zz", "fake@example.invalid", "https://x.invalid/v1"
VARIANTS = {
    "polytope-layout": (json.dumps({"user_key": FAKE_KEY, "user_email": FAKE_EMAIL}), "user_key"),
    "ecmwf-layout-without-url": (json.dumps({"key": FAKE_KEY, "email": FAKE_EMAIL}), "email, key"),
    "invalid-json": ('{"url": "' + FAKE_URL + '", "key": "' + FAKE_KEY + '",', None),
    "empty": ("", None),
    "cds-yaml": (f"url: {FAKE_URL}\nkey: {FAKE_KEY}\n", "key, url"),
    "json-list": ("[1, 2]", None),
    "not-utf8": (b"\xff\xfe\x00\x81" + FAKE_KEY.encode(), None),
}
# script, arguments, file, variants that are valid for that file (and so not malformed)
CHECKS = {
    "mars": ("ecmwf-mars/scripts/mars.py", ["check"], ".ecmwfapirc", ()),
    "ptpoint": (
        "ecmwf-polytope/scripts/ptpoint.py",
        ["--check", "--offline"],
        ".polytopeapirc",
        ("polytope-layout",),
    ),
    "ptpoint-ecmwfapirc-fallback": (
        "ecmwf-polytope/scripts/ptpoint.py",
        ["--check", "--offline"],
        ".ecmwfapirc",
        ("ecmwf-layout-without-url",),
    ),
    "cds": (
        "ecmwf-cds-ads/scripts/cds.py",
        ["check"],
        ".cdsapirc",
        ("ecmwf-layout-without-url", "cds-yaml"),
    ),
    "destine": (
        "ecmwf-destine/scripts/destine.py",
        ["check"],
        ".polytopeapirc-destine",
        ("polytope-layout",),
    ),
}
CASES = [
    (check, variant)
    for check, (_, _, _, valid) in CHECKS.items()
    for variant in VARIANTS
    if variant not in valid
]


@pytest.mark.parametrize("check,variant", CASES, ids=[f"{c}-{v}" for c, v in CASES])
def test_malformed_credentials_never_crash_or_leak(tmp_path, check, variant):
    script, args, name, _ = CHECKS[check]
    content, keys = VARIANTS[variant]
    (tmp_path / name).write_bytes(content if isinstance(content, bytes) else content.encode())
    out = subprocess.run(
        [sys.executable, str(SKILLS / script), *args],
        capture_output=True,
        text=True,
        timeout=60,
        # a dead proxy: an accidental request fails fast instead of going online
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin", "https_proxy": "http://127.0.0.1:9"},
    )
    both = out.stdout + out.stderr
    assert "Traceback" not in both, out.stderr
    assert out.returncode == 4, both
    assert f"BLOCKED: ~/{name} is malformed" in out.stderr
    if keys:
        assert keys in out.stderr
    for value in (FAKE_KEY, FAKE_EMAIL, FAKE_URL):
        assert value not in both
