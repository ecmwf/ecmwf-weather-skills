# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Install routes for other harnesses (pi packages) and read-only skill installs."""

import json
import re
import shutil
import subprocess
import sys

from conftest import ROOT, SKILLS


def test_root_package_json_is_a_pi_package_listing_the_skills():
    pkg = json.loads((ROOT / "package.json").read_text())
    assert pkg["private"] is True and "pi-package" in pkg["keywords"]
    assert pkg["license"] == "Apache-2.0"
    (path,) = pkg["pi"]["skills"]
    skills = sorted(p.parent.name for p in (ROOT / path).glob("*/SKILL.md"))
    assert skills == sorted(p.parent.name for p in SKILLS.glob("*/SKILL.md")) and len(skills) == 8


def test_scripts_importing_siblings_never_write_bytecode():
    for f in SKILLS.glob("*/scripts/*.py"):
        text = f.read_text()
        siblings = {p.stem for p in f.parent.glob("*.py")} - {f.stem}
        m = re.search(rf"^\s*import ({'|'.join(siblings)})\b", text, re.M) if siblings else None
        if m:
            guard = text.find("sys.dont_write_bytecode = True")
            assert 0 <= guard < m.start(), (
                f"{f}: set sys.dont_write_bytecode before sibling imports"
            )


def test_running_a_script_leaves_the_skill_untouched(tmp_path):
    skill = tmp_path / "ecmwf-open-data"
    shutil.copytree(SKILLS / "ecmwf-open-data", skill, ignore=shutil.ignore_patterns("__pycache__"))
    for script in ("odpoint.py", "odcatalog.py"):
        out = subprocess.run(
            [sys.executable, str(skill / "scripts" / script), "--help"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert out.returncode == 0, out.stderr
    assert not list(skill.rglob("__pycache__"))
