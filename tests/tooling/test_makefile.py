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
    "check-endpoints",
    "release-prepare",
    "release-publish",
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


def test_release_prepare_runs_every_gate_then_bumps_and_opens_a_pr():
    out = make("-n", "release-prepare", "VERSION=9.9.9")
    assert out.returncode == 0, out.stderr
    o = out.stdout
    order = [
        "release.py preflight 9.9.9",
        "pytest",
        "check_endpoints.py",
        "regenerate_references.py --check",
        "git switch -c release/9.9.9",
        "bump_version.py --set 9.9.9",
        "release.py changelog 9.9.9",
        "scripts/gen_docs.py\n",  # the regenerate step, not the earlier --check
        'git commit -m "chore: release 9.9.9"',
        "gh pr create",
    ]
    pos = [o.find(x) for x in order]
    assert all(p >= 0 for p in pos), [x for x, p in zip(order, pos, strict=True) if p < 0]
    assert pos == sorted(pos), "steps out of order"


def test_release_publish_tags_only_after_checks():
    o = make("-n", "release-publish", "VERSION=9.9.9").stdout
    pos = [
        o.find(x)
        for x in (
            "release.py publish-check 9.9.9",
            "git tag -a 9.9.9",
            "git push origin 9.9.9",
            "gh release create 9.9.9",
        )
    ]
    assert all(p >= 0 for p in pos) and pos == sorted(pos)


def test_release_targets_require_a_version():
    for t in ("release-prepare", "release-publish"):
        out = make(t)
        assert out.returncode != 0 and "VERSION=X.Y.Z" in out.stdout + out.stderr
