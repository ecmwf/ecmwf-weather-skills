# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Every runnable Python block in the skills' references runs, against small fixtures.

The references are what agents copy; they must stay correct for the installed earthkit
versions. Blocks marked `# needs network` run only with -m live; `# needs credentials` never.
"""

import re
import shutil
import subprocess

import pytest
from conftest import FIXTURES, SKILLS

REFERENCES = sorted(SKILLS.glob("*/references/*.md"))
RECIPES = SKILLS / "ecmwf-earthkit" / "references" / "recipes.md"


def _sections(path):
    return re.findall(r"^## (.+?)$|```python\n(.*?)```", path.read_text(), re.M | re.S)


def _blocks():
    out = []
    for path in REFERENCES:
        section = path.stem
        n = 0
        for heading, code in _sections(path):
            if heading:
                section, n = heading, 0
            elif code.startswith("# /// script") and "# needs credentials" not in code:
                n += 1
                live = "# needs network" in code
                out.append(
                    pytest.param(
                        code,
                        id=f"{path.parent.parent.name}: {section} #{n}",
                        marks=[pytest.mark.live] if live else [],
                    )
                )
    return out


def test_every_recipe_block_declares_its_dependencies():
    for _, code in _sections(RECIPES):
        if code:
            assert code.startswith("# /// script\n") and "# dependencies = [" in code, code[:80]


@pytest.mark.earthkit
@pytest.mark.parametrize("code", _blocks())
def test_recipe_runs(code, tmp_path):
    shutil.copy(FIXTURES / "ifs-sfc-5deg-20261002-00z.grib2", tmp_path / "forecast.grib2")
    shutil.copy(FIXTURES / "ifs-ens-2t-5members-5deg.grib2", tmp_path / "ens.grib2")
    shutil.copy(FIXTURES / "era5-lisbon-t2m-2020-01.csv", tmp_path / "lisbon.csv")
    script = tmp_path / "recipe.py"
    script.write_text(code)
    out = subprocess.run(
        ["uv", "run", "--quiet", "--script", str(script)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=900,
    )
    assert out.returncode == 0, out.stderr[-3000:]
