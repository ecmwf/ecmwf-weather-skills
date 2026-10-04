# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Every script turns an unreachable ECMWF service into a BLOCKED report with ECMWF's status."""

import subprocess
import sys

import pytest
from conftest import FIXTURES, SKILLS, load_script

# A dead proxy makes every HTTPS request fail, including the status lookup ("unknown").
OFFLINE = {
    "PATH": "/usr/bin:/bin",
    "HTTPS_PROXY": "http://127.0.0.1:9",
    "https_proxy": "http://127.0.0.1:9",
}


@pytest.mark.parametrize(
    "skill, script, args",
    [
        ("open-data", "odcatalog.py", ["latest"]),
        ("opencharts-wms", "wms.py", ["layers", "--public"]),
        ("opencharts-wms", "opencharts.py", ["search", "temperature"]),
        ("cds-ads", "cds.py", ["search", "era5"]),
    ],
)
def test_unreachable_service_is_blocked_with_status(tmp_path, skill, script, args):
    out = subprocess.run(
        [sys.executable, str(SKILLS / skill / "scripts" / script), *args],
        capture_output=True,
        text=True,
        timeout=120,
        env={**OFFLINE, "HOME": str(tmp_path)},
    )
    assert out.returncode == 2, out.stderr
    assert "BLOCKED: cannot reach" in out.stderr and "status.ecmwf.int" in out.stderr


@pytest.mark.parametrize(
    "skill, script", [("mars", "mars"), ("polytope", "ptpoint"), ("destine", "destine")]
)
def test_connection_errors_map_to_network_barrier(monkeypatch, skill, script):
    import json

    m = load_script(skill, script)
    status = json.loads((FIXTURES / "ecmwf-status.json").read_text())
    monkeypatch.setattr(
        m.ecmwf_status, "_get", lambda url: status if "/status/status" in url else {"nodes": []}
    )
    b = m.barrier_for_error("HTTPSConnectionPool: Max retries exceeded (Connection refused)")
    assert b["blocked"].startswith("cannot reach") and "ECMWF status" in b["why"]
