# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0
"""findlibs 0.1.4 fixes earthkit-data#1202 (a process that imports pyproj after ecCodes aborts at
exit): every bundle that loads ecCodes asks for it, and nothing works around the old behaviour."""

import re

import pytest
import tomllib
from conftest import ROOT, SKILLS

FLOOR = "findlibs>=0.1.4"
# A bundle loads ecCodes through earthkit-data or pdbufr; both find the library with findlibs.
ECCODES_USERS = ("earthkit-data", "pdbufr")
SCRIPTS = sorted(SKILLS.glob("*/scripts/*.py"))
REFERENCES = sorted(SKILLS.glob("*/references/*.md"))
SKILL_FILES = sorted(SKILLS.glob("*/SKILL.md")) + REFERENCES
PEP723 = re.compile(r"(?ms)^# /// script\s*$\n(?P<body>.*?)^# ///\s*$")


def _bundles(text):
    for block in PEP723.finditer(text):
        body = block["body"]
        if any(f'"{name}' in body for name in ECCODES_USERS):
            yield body


def _cases():
    for path in SCRIPTS + REFERENCES:
        for n, body in enumerate(_bundles(path.read_text()), 1):
            yield pytest.param(body, id=f"{path.parent.parent.name}/{path.name}#{n}")


def test_bundles_are_found():
    assert len(list(_cases())) >= 15


@pytest.mark.parametrize("body", list(_cases()))
def test_bundle_asks_for_the_findlibs_fix(body):
    assert f'"{FLOOR}"' in body


def test_earthkit_group_asks_for_the_findlibs_fix():
    group = tomllib.loads((ROOT / "pyproject.toml").read_text())["dependency-groups"]["earthkit"]
    assert FLOOR in group


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_install_hint_asks_for_the_findlibs_fix(path):
    text = path.read_text()
    if "pip install" in text and any(name in text for name in ECCODES_USERS):
        assert FLOOR in text, f"{path.name}: its pip install hint lacks {FLOOR!r}"


@pytest.mark.parametrize("path", SCRIPTS, ids=lambda p: p.name)
def test_no_script_loads_pyproj_first(path):
    assert "load_pyproj_first" not in path.read_text()


@pytest.mark.parametrize("path", SKILL_FILES, ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_skill_text_tells_agents_to_import_pyproj_first(path):
    text = path.read_text()
    assert not re.search(r"import pyproj\s+#\s*first", text)
    assert "Import order" not in text
    assert "pyproj` first" not in text
    assert "pyproj` before" not in text
