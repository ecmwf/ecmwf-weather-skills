# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""The top-level Makefile is the single, deterministic entry point."""

import json
import shutil
import subprocess

import pytest
from conftest import ROOT

pytestmark = pytest.mark.skipif(shutil.which("make") is None, reason="make not installed")
REQUIRED = [
    "help",
    "all",
    "test",
    "test-earthkit",
    "test-live",
    "lint",
    "check",
    "docs",
    "version",
    "version-check",
    "evals",
    "references",
    "dist",
    "clean",
    "setup",
]


def make(*args, cwd=ROOT):
    return subprocess.run(
        ["make", "--no-print-directory", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_help_lists_every_required_target_with_a_description():
    out = make("help")
    assert out.returncode == 0, out.stderr
    for t in REQUIRED:
        line = next(
            (ln for ln in out.stdout.splitlines() if ln.split() and ln.split()[0] == t),
            None,
        )
        assert line and len(line.split()) > 1, (
            f"target {t!r} missing or undocumented in `make help`"
        )


def test_default_goal_is_help():
    assert make().stdout == make("help").stdout


def test_version_without_argument_prints_current():
    cur = json.loads((ROOT / "plugins/ecmwf-weather/.claude-plugin/plugin.json").read_text())[
        "version"
    ]
    out = make("version")
    assert out.returncode == 0 and out.stdout.strip() == cur


@pytest.mark.parametrize("args", [["version", "1.2.3"], ["version", "VERSION=1.2.3"]])
def test_version_dry_run_calls_bump_script(args):
    out = make("-n", *args)
    assert out.returncode == 0, out.stderr
    assert "scripts/bump_version.py --set 1.2.3" in out.stdout


@pytest.mark.parametrize("bad", ["1.2", "v1.2.3", "1.2.3.4", "x"])
def test_version_rejects_non_semver_without_touching_files(bad):
    before = (ROOT / "plugins/ecmwf-weather/.claude-plugin/plugin.json").read_text()
    out = make("version", f"VERSION={bad}")
    assert out.returncode != 0 and "MAJOR.MINOR.MICRO" in (out.stdout + out.stderr)
    assert (ROOT / "plugins/ecmwf-weather/.claude-plugin/plugin.json").read_text() == before


def test_all_is_lint_check_test():
    out = make("-n", "all")
    assert out.returncode == 0
    for frag in ("check_skills.py", "validate_packaging.py", "pytest"):
        assert frag in out.stdout


def test_clean_dry_run_never_deletes_tracked_sources():
    out = make("-n", "clean")
    assert "dist" in out.stdout and "evals/runs" in out.stdout
    for protected in ("plugins", "tests/fixtures", "scripts"):
        assert f"rm -rf {protected}" not in out.stdout
