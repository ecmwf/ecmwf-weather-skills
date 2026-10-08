# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import shutil
import zipfile

import build_dist
import bump_version
import pytest
import regenerate_references as regen
import validate_packaging as vp
from conftest import FIXTURES, ROOT


@pytest.fixture
def repo(tmp_path):
    """A copy of the packaging-relevant parts of the repo."""
    dst = tmp_path / "repo"
    shutil.copytree(ROOT / ".claude-plugin", dst / ".claude-plugin")
    shutil.copytree(
        ROOT / "plugins",
        dst / "plugins",
        ignore=shutil.ignore_patterns("__pycache__", ".cache"),
    )
    return dst


# --- validate_packaging ---------------------------------------------------------------------------


def test_repo_packaging_is_valid():
    assert vp.problems(ROOT) == []


def test_detects_manifest_drift(repo):
    p = repo / "plugins/ecmwf-weather/.codex-plugin/plugin.json"
    d = json.loads(p.read_text())
    d["version"] = "9.9.9"
    d["license"] = "MIT"
    p.write_text(json.dumps(d))
    probs = "\n".join(vp.problems(repo))
    assert "version" in probs and "license" in probs


def test_detects_skill_version_and_name_drift(repo):
    s = repo / "plugins/ecmwf-weather/skills/ecmwf-mars/SKILL.md"
    s.write_text(
        s.read_text()
        .replace('version: "', 'version: "7')
        .replace("name: ecmwf-mars", "name: ecmwf-marz")
    )
    probs = "\n".join(vp.problems(repo))
    assert "mars" in probs and "version" in probs and "name" in probs


def test_detects_marketplace_source_and_display_name(repo):
    m = repo / ".claude-plugin/marketplace.json"
    d = json.loads(m.read_text())
    d["plugins"][0]["source"] = "./plugins/nope"
    d["plugins"][0]["displayName"] = "Other"
    m.write_text(json.dumps(d))
    probs = "\n".join(vp.problems(repo))
    assert "source" in probs and "displayName" in probs


# --- bump_version ---------------------------------------------------------------------------------


def test_bump_patch_everywhere(repo):
    old = bump_version.current(repo)
    new = bump_version.bump(repo, level="patch")
    major, minor, patch = map(int, old.split("."))
    assert new == f"{major}.{minor}.{patch + 1}"
    assert vp.problems(repo) == [] and bump_version.current(repo) == new


def test_bump_set_and_refuse_on_drift(repo):
    assert bump_version.bump(repo, set_to="1.0.0") == "1.0.0"
    s = repo / "plugins/ecmwf-weather/skills/ecmwf-mars/SKILL.md"
    s.write_text(s.read_text().replace('version: "1.0.0"', 'version: "0.0.1"'))
    with pytest.raises(SystemExit):
        bump_version.bump(repo, level="minor")


# --- build_dist -----------------------------------------------------------------------------------


def test_build_dist(repo, tmp_path):
    out = tmp_path / "dist"
    files = build_dist.build(repo, out)
    names = {f.name for f in files}
    assert names == {"ecmwf-weather-claude.zip", "ecmwf-weather-openai.zip"}
    claude = zipfile.ZipFile(out / "ecmwf-weather-claude.zip")
    entries = claude.namelist()
    assert any(e.endswith("skills/ecmwf-open-data/SKILL.md") for e in entries)
    assert not any("/bin/" in e for e in entries) and not any(".cache" in e for e in entries)
    openai = zipfile.ZipFile(out / "ecmwf-weather-openai.zip")
    skill = next(e for e in openai.namelist() if e.endswith("skills/ecmwf-mars/SKILL.md"))
    text = openai.read(skill).decode()
    assert "metadata:" not in text and "name: ecmwf-mars" in text
    assert any(e.endswith(".codex-plugin/plugin.json") for e in openai.namelist())


# --- regenerate_references ------------------------------------------------------------------------

PARAMDB = {
    "2t": {"name": "2 metre temperature", "units": "K"},
    "tp": {"name": "Total precipitation", "units": "m"},
    "t": {"name": "Temperature", "units": "K"},
}


def test_render_fields_md():
    from conftest import load_script

    od = load_script("ecmwf-open-data", "odcatalog")
    ifs = od.summarise_fields(od.parse_index((FIXTURES / "ifs-oper-24h.index").read_text()))
    aifs = od.summarise_fields(od.parse_index((FIXTURES / "aifs-oper-24h.index").read_text()))
    md = regen.render_fields({"ifs": ifs, "aifs-single": aifs}, PARAMDB)
    assert md.startswith("<!--\nSPDX-FileCopyrightText: 2026 European Centre")
    # REUSE-IgnoreStart — asserts on generated output
    assert "SPDX-License-Identifier: Apache-2.0" in md and "GENERATED" in md
    # REUSE-IgnoreEnd
    assert "## Contents" in md
    assert "| `2t` | 2 metre temperature | K |" in md
    assert "1000, 925, 850" in md


def test_render_layers_md():
    from conftest import load_script

    wms = load_script("ecmwf-opencharts-wms", "wms")
    layers = wms.parse_capabilities((FIXTURES / "wms-capabilities-trimmed.xml").read_text())
    md = regen.render_layers(layers)
    assert "| `msl_public` | Mean sea level pressure (Public) | yes | `ct_blk_i5_t2` |" in md
    assert "| `background` | Background | no |" in md
    assert "2026-" not in md  # no volatile valid times


def test_render_versions_md():
    info = {"earthkit-data": "1.2.3", "earthkit-time": "0.1.8", "thermofeel": "2.3.0"}
    md = regen.render_versions(info)
    assert "| `earthkit-data` | 1.2.3 | yes |" in md
    assert "| `earthkit-time` | 0.1.8 | exception | Emerging" in md  # allowed, pinned


def test_check_mode_reports_drift(tmp_path):
    f = tmp_path / "x.md"
    f.write_text("old")
    assert regen.write_or_check({f: "new"}, check=True) == [f]
    assert f.read_text() == "old"
    assert regen.write_or_check({f: "new"}, check=False) == [f]
    assert f.read_text() == "new"
    assert regen.write_or_check({f: "new"}, check=True) == []


def test_render_fields_is_independent_of_upstream_order():
    a = {"params": {"sfc": ["2t", "tp"], "pl": ["t"]}, "pressure_levels": [850]}
    b = {"params": {"pl": ["t"], "sfc": ["tp", "2t"]}, "pressure_levels": [850]}
    assert regen.render_fields({"ifs": a}, PARAMDB) == regen.render_fields({"ifs": b}, PARAMDB)
