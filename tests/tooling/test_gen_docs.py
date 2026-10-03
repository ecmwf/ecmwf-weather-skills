# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0

import gen_docs
from conftest import ROOT, SKILLS


def test_catalogue_lists_every_skill_script_and_reference():
    md = gen_docs.render(SKILLS)
    # REUSE-IgnoreStart — asserts on generated output
    assert "SPDX-License-Identifier: Apache-2.0" in md and "GENERATED" in md
    # REUSE-IgnoreEnd
    for skill in (
        "open-data",
        "earthkit",
        "opencharts-wms",
        "polytope",
        "cds-ads",
        "mars",
    ):
        assert f"## `{skill}`" in md
    assert "`scripts/odpoint.py`" in md and "`references/catalog.md`" in md
    assert "Finds, downloads and decodes free ECMWF" in md  # description comes from SKILL.md


def test_render_is_deterministic():
    assert gen_docs.render(SKILLS) == gen_docs.render(SKILLS)


def test_committed_catalogue_is_up_to_date():
    assert (ROOT / "docs" / "skills.md").read_text() == gen_docs.render(SKILLS), (
        "docs/skills.md is stale — run `make docs`"
    )
