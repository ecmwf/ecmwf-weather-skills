# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Every pull request must carry the CLA declaration, even when opened without the template."""

import ensure_cla
from conftest import ROOT

TEMPLATE = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()


def test_section_is_read_from_the_template():
    s = ensure_cla.cla_section(TEMPLATE)
    assert (
        s.startswith("## Contributor Licence Agreement") and "Contributor-License-Agreement.md" in s
    )
    assert "## Checklist" not in s


def test_appended_when_missing():
    out = ensure_cla.with_cla("Fixes the meteogram axis.", TEMPLATE)
    assert out.startswith("Fixes the meteogram axis.")
    assert out.rstrip().endswith(ensure_cla.cla_section(TEMPLATE).rstrip())


def test_empty_body_gets_the_declaration():
    assert ensure_cla.with_cla("", TEMPLATE).strip() == ensure_cla.cla_section(TEMPLATE).strip()
    assert ensure_cla.with_cla(None, TEMPLATE).strip() == ensure_cla.cla_section(TEMPLATE).strip()


def test_unchanged_when_present_so_edits_do_not_loop():
    body = ensure_cla.with_cla("text", TEMPLATE)
    assert ensure_cla.with_cla(body, TEMPLATE) == body
    assert ensure_cla.with_cla(TEMPLATE, TEMPLATE) == TEMPLATE


def test_altered_declaration_is_restored():
    body = TEMPLATE.replace("I agree to the terms", "I do not agree to the terms")
    assert ensure_cla.cla_section(TEMPLATE).strip() in ensure_cla.with_cla(body, TEMPLATE)


def test_workflow_never_runs_pull_request_code():
    wf = (ROOT / ".github" / "workflows" / "cla.yml").read_text()
    assert "pull_request_target:" in wf and "opened, edited, reopened" in wf
    assert "github.event.pull_request.base.sha" in wf
    assert "head.sha" not in wf and "head.ref" not in wf
    assert "python3 scripts/ensure_cla.py" in wf
