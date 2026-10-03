# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""ECMWF open-sourcing rules (ecmwf/codex Legal/): licence files, notices, CLA in PRs."""

import re

from conftest import ROOT

INTERGOV = (
    "In applying this licence, ECMWF does not waive the privileges and immunities\n"
    "granted to it by virtue of its status as an intergovernmental organisation\n"
    "nor does it submit to any jurisdiction."
)
CLA_URL = "https://github.com/ecmwf/codex/blob/main/Legal/Contributor-License-Agreement.md"
# Built at runtime so this file does not match its own scan.
LEGACY = ("GNU " + "General Public", "L" + "GPL", "ECMWF " + "licence agreement")
HOLDER = "European Centre for Medium-Range Weather Forecasts (ECMWF)"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def test_license_is_unmodified_apache_plus_intergovernmental_tail():
    apache = (ROOT / "LICENSES" / "Apache-2.0.txt").read_text()
    lic = (ROOT / "LICENSE").read_text()
    assert "Apache License" in apache and "Version 2.0, January 2004" in apache
    assert lic.rstrip().endswith(INTERGOV)
    body = lic[
        : lic.rindex(
            "-------------------------------------------------------------------------------"
        )
    ]
    assert _norm(body) == _norm(apache), "Apache text in LICENSE must be unmodified"


def test_notice_carries_copyright_and_intergovernmental_notice():
    notice = (ROOT / "NOTICE").read_text()
    assert f"Copyright 2026 {HOLDER}" in notice
    assert "https://www.ecmwf.int" in notice and INTERGOV in notice


def test_contributors_file_lists_people():
    text = (ROOT / "CONTRIBUTORS").read_text()
    assert "Tiago Quintino" in text


def test_reuse_toml_covers_uncommentable_files():
    t = (ROOT / "REUSE.toml").read_text()
    assert '"LICENSE"' in t and '"NOTICE"' in t and "tests/fixtures/**" in t
    assert HOLDER in t and "Apache-2.0" in t


def test_no_legacy_licences_mentioned():
    for p in ROOT.rglob("*"):
        if (
            p.is_file()
            and p.suffix in (".py", ".md", ".js", ".html", ".css", ".yml", ".toml")
            and ".venv" not in p.parts
            and "evals" not in p.parts
        ):
            text = p.read_text(errors="replace")
            assert not any(b in text for b in LEGACY), p


DECLARATION = (
    "By submitting this pull request, I confirm that my contribution is made under\n"
    "the terms of the [Apache License 2.0](LICENSE) and that I have the right to\n"
    "submit it under this licence. I agree to the terms of the\n"
    f"[ECMWF Contributor Licence Agreement]({CLA_URL})."
)


def test_pr_template_ends_with_the_ecmwf_cla_declaration():
    t = (ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md").read_text()
    assert t.rstrip().endswith("## Contributor Licence Agreement\n\n" + DECLARATION)
    assert "make all" in t


def test_cla_link_points_at_the_existing_codex_file():
    # Other ECMWF repos link a lowercase file name that does not exist in ecmwf/codex.
    assert CLA_URL.endswith("/Legal/Contributor-License-Agreement.md")


def test_contributing_security_and_conduct_exist():
    assert CLA_URL in (ROOT / "CONTRIBUTING.md").read_text()
    assert "support.ecmwf.int" in (ROOT / "SECURITY.md").read_text()
    assert (ROOT / "CODE_OF_CONDUCT.md").exists()


def test_claude_md_is_a_symlink_to_agents_md():
    c = ROOT / "CLAUDE.md"
    assert c.is_symlink() and c.resolve() == (ROOT / "AGENTS.md").resolve()


def test_readme_has_maturity_badge_support_and_licence():
    r = (ROOT / "README.md").read_text()
    assert "Project%20Maturity/emerging_badge.svg" in r
    assert "This software is **Emerging**" in r
    assert "support.ecmwf.int" in r and "best-effort" in r.lower()
    assert INTERGOV.replace("\n", " ") in r.replace("\n", " ")


def test_github_actions_are_pinned_to_commit_shas():
    for wf in (ROOT / ".github" / "workflows").glob("*.yml"):
        for ref in re.findall(r"uses:\s*(\S+)", wf.read_text()):
            pinned = re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref)
            assert pinned, f"{wf.name}: {ref} is not pinned to a commit SHA"


def test_codeowners_assigns_the_maintainer():
    text = (ROOT / ".github" / "CODEOWNERS").read_text()
    rules = [ln.split() for ln in text.splitlines() if ln.strip() and not ln.startswith("#")]
    assert ["*", "@tlmquintino"] in rules
