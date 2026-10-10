# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0
"""PEP 723 metadata of the skill scripts: one Python floor, SciPy pinned where it is pulled in,
and source that still parses on the oldest system Python (macOS ships 3.9)."""

import ast
import re

import pytest
import tomllib
from conftest import ROOT, SKILLS

SCRIPTS = sorted(SKILLS.glob("*/scripts/*.py"))
BLOCK = re.compile(r"(?m)^# /// script\s*$\n(?P<body>(?:^#(?: .*)?$\n)+?)^# ///\s*$")
# earthkit-geo and earthkit-transforms depend on SciPy; SciPy 1.15 (the newest for Python 3.10)
# fails to load on macOS 27 ("__DATA/__thread_bss has a zero-fill section type").
SCIPY_PULLERS = ("earthkit-geo", "earthkit-transforms")
SCIPY_PIN = "scipy>=1.16"


def _metadata(path):
    m = BLOCK.search(path.read_text())
    if not m:
        return None
    toml = "\n".join(line[2:] if line.startswith("# ") else "" for line in m["body"].splitlines())
    return tomllib.loads(toml)


def _pyproject():
    return tomllib.loads((ROOT / "pyproject.toml").read_text())


WITH_METADATA = [p for p in SCRIPTS if _metadata(p)]


def test_scripts_are_found():
    assert len(WITH_METADATA) >= 8


@pytest.mark.parametrize("path", WITH_METADATA, ids=lambda p: p.name)
def test_requires_python_matches_pyproject(path):
    assert _metadata(path)["requires-python"] == _pyproject()["project"]["requires-python"]


def test_python_floor_is_3_12():
    assert _pyproject()["project"]["requires-python"] == ">=3.12"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_scipy_is_pinned_where_earthkit_pulls_it_in(path):
    meta = _metadata(path) or {}
    deps = meta.get("dependencies", [])
    if any(d.startswith(SCIPY_PULLERS) for d in deps):
        assert SCIPY_PIN in deps, f"{path.name} needs {SCIPY_PIN!r}"


def test_earthkit_group_mirrors_the_scipy_pin():
    assert SCIPY_PIN in _pyproject()["dependency-groups"]["earthkit"]


def test_ruff_target_stays_below_the_floor():
    # Standard-library scripts are the fallback for machines whose only Python is the system
    # one (macOS: 3.9); ruff must not rewrite them into newer-only syntax.
    assert _pyproject()["tool"]["ruff"]["target-version"] == "py310"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_scripts_parse_with_the_python_3_9_grammar(path):
    ast.parse(path.read_text(), filename=str(path), feature_version=(3, 9))
