# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Lint every skill against the Anthropic skill-authoring best practices (see AGENTS.md)."""

from pathlib import Path

import check_skills as cs
import pytest
from conftest import SKILLS

GOOD = """---
name: demo-skill
description: Processes demo files and reports results. Use when the user mentions demo files.
license: Apache-2.0
metadata:
  version: "0.1.0"
---

# Demo

## Contents
- Quick start
- Details — `references/details.md`

## Quick start
Run `scripts/demo.py` from the user's working directory; write files there, never inside the
skill directory.
"""


def make_skill(tmp_path: Path, text: str, refs: dict[str, str] | None = None) -> Path:
    d = tmp_path / "demo-skill"
    (d / "references").mkdir(parents=True)
    (d / "SKILL.md").write_text(text)
    for name, body in (refs or {"details.md": "# Details\nshort\n"}).items():
        (d / "references" / name).write_text(body)
    return d


def test_good_skill_has_no_problems(tmp_path):
    assert cs.check_skill(make_skill(tmp_path, GOOD)) == []


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda s: s.replace("name: demo-skill", "name: Demo_Skill"), "name"),
        (lambda s: s.replace("name: demo-skill", "name: claude-demo"), "reserved"),
        (lambda s: s.replace("Processes demo", "Process demo"), "third person"),
        (lambda s: s.replace("Processes demo", "I can process demo"), "third person"),
        (lambda s: s.replace("Use when the user", "Use when: the user"), "': '"),
        (lambda s: s.replace("## Contents", "## Stuff"), "table of contents"),
        (lambda s: s + "See references\\details.md\n", "backslash"),
        (lambda s: s.replace("never inside the\nskill directory", "anywhere"), "skill directory"),
        (lambda s: s.replace("references/details.md", "nothing"), "not linked"),
    ],
)
def test_violations_detected(tmp_path, mutate, expected):
    problems = cs.check_skill(make_skill(tmp_path, mutate(GOOD)))
    assert any(expected in p for p in problems), problems


def test_long_description_rejected(tmp_path):
    long = GOOD.replace("Use when the user mentions demo files.", "Use when " + "x " * 600)
    assert any("1024" in p for p in cs.check_skill(make_skill(tmp_path, long)))


def test_long_body_rejected(tmp_path):
    long = GOOD + "line\n" * 520
    assert any("500" in p for p in cs.check_skill(make_skill(tmp_path, long)))


def test_long_reference_needs_contents(tmp_path):
    refs = {"details.md": "# Details\n" + "x\n" * 120}
    assert any(
        "details.md" in p and "table of contents" in p
        for p in cs.check_skill(make_skill(tmp_path, GOOD, refs))
    )


def test_nested_reference_links_rejected(tmp_path):
    refs = {
        "details.md": "# Details\nSee references/other.md\n",
        "other.md": "# Other\n",
    }
    text = GOOD.replace(
        "- Details — `references/details.md`",
        "- Details — `references/details.md`, `references/other.md`",
    )
    assert any("one level" in p for p in cs.check_skill(make_skill(tmp_path, text, refs)))


def test_time_sensitive_wording_flagged(tmp_path):
    bad = GOOD + "Since 13 May 2026 the stream changed.\n"
    assert any("time-sensitive" in p for p in cs.check_skill(make_skill(tmp_path, bad)))


def test_time_sensitive_allowed_in_old_patterns(tmp_path):
    ok = GOOD + "\n## Old patterns\nBefore 13 May 2026 the stream was scda.\n"
    assert cs.check_skill(make_skill(tmp_path, ok)) == []


@pytest.mark.parametrize(
    "skill", sorted(p.name for p in SKILLS.iterdir() if (p / "SKILL.md").exists())
)
def test_repo_skills_pass_lint(skill):
    assert cs.check_skill(SKILLS / skill) == []
