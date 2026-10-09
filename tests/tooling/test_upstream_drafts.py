# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Draft upstream issues: complete, with a self-contained reproducer each; drafts never filed
without a URL recorded."""

import subprocess

import pytest
import tomllib
from conftest import ROOT

UP = ROOT / "docs" / "upstream"
DATA = tomllib.loads((UP / "issues.toml").read_text())
ISSUES = DATA["issue"]


@pytest.mark.parametrize("i", ISSUES, ids=lambda i: i["script"])
def test_draft_is_complete(i):
    for key in ("repo", "kind", "status", "title", "expected", "actual", "suggestion"):
        assert i.get(key), key
    assert i["repo"].startswith("ecmwf/earthkit-") and i["kind"] in ("bug", "feature")
    assert i["status"] in ("draft", "filed", "fixed")
    if i["status"] != "draft":
        assert i.get("url", "").startswith("https://github.com/ecmwf/")
    code = (UP / i["script"]).read_text()
    assert code.startswith("# /// script\n") and "==" in code  # pinned versions
    assert "REPRODUCED" in code


def test_every_reproducer_is_listed():
    listed = {i["script"] for i in ISSUES}
    assert {p.name for p in UP.glob("*.py")} == listed


def test_readme_is_generated():
    out = subprocess.run(
        ["python3", str(ROOT / "scripts" / "upstream_drafts.py"), "--check"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stdout + out.stderr


@pytest.mark.live
@pytest.mark.parametrize(
    "i", [i for i in ISSUES if i["status"] == "draft"], ids=lambda i: i["script"]
)
def test_draft_still_reproduces(i):
    out = subprocess.run(
        ["uv", "run", "--quiet", "--script", str(UP / i["script"])],
        capture_output=True,
        text=True,
        timeout=900,
        cwd=UP,
    )
    assert "REPRODUCED" in out.stdout and "NOT REPRODUCED" not in out.stdout, (
        out.stdout,
        out.stderr[-500:],
    )


@pytest.mark.parametrize("pr", DATA.get("pr", []), ids=lambda p: p["url"])
def test_pull_request_is_recorded(pr):
    assert pr["repo"].startswith("ecmwf/earthkit-") and pr["title"]
    assert pr["url"].startswith(f"https://github.com/{pr['repo']}/pull/")
    assert pr["status"] in ("open", "merged", "closed")
