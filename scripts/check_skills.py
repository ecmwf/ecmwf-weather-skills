#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Lint skills against Anthropic's skill-authoring best practices.

https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices

Checks what can be checked mechanically:
  - frontmatter: name (<=64, lowercase/digits/hyphens, matches directory, no reserved words),
    description (non-empty, <=1024 chars, third person, no ': ' which breaks YAML scalars)
  - SKILL.md body < 500 lines and starts with a "## Contents" table of contents
  - every references/*.md is linked from SKILL.md; references don't link other references
    (one level deep); references over 100 lines have a "## Contents" section
  - no Windows-style backslash paths
  - no time-sensitive wording outside an "## Old patterns" section

  python3 scripts/check_skills.py            # all skills, exit 1 on problems
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILLS = ROOT / "plugins" / "ecmwf-weather" / "skills"

MAX_NAME, MAX_DESC, MAX_BODY_LINES, REF_TOC_LINES = 64, 1024, 500, 100
RESERVED = ("anthropic", "claude")
# Imperative first words mean the description isn't third person ("Process files" vs "Processes").
FIRST_PERSON = re.compile(r"^(I|We|You|My|Our)\b", re.I)
TIME_SENSITIVE = re.compile(
    r"\b(since|before|after|until|as of)\s+(\d{1,2}\s+)?(January|February|March|April|May|June|"
    r"July|August|September|October|November|December)\b"
    r"|\b(since|before|after|until|as of)\s+20\d\d",
    re.I,
)
REF_LINK = re.compile(r"references/([A-Za-z0-9_.-]+\.md)")
BACKSLASH_PATH = re.compile(r"\b(?:scripts|references)\\")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return {}, text
    fm: dict[str, str] = {}
    for line in m.group(1).splitlines():
        km = re.match(r"^([A-Za-z_]+):\s?(.*)$", line)
        if km:
            fm[km.group(1)] = km.group(2).strip()
    return fm, m.group(2)


def _third_person(desc: str) -> bool:
    if FIRST_PERSON.match(desc):
        return False
    first = re.match(r"[A-Za-z-]+", desc)
    # third-person verbs end in -s ("Finds", "Reads", "Builds"); allow noun-phrase openings
    # that start with a capitalised proper noun/acronym ("ECMWF ...").
    w = first.group(0) if first else ""
    # "Processes"/"Finds" yes; "Process"/"Access" (imperative ending in -ss) no.
    return bool(w) and ((w.endswith("s") and not w.endswith("ss")) or w.isupper())


def _strip_old_patterns(body: str) -> str:
    return re.split(r"^## Old patterns\s*$", body, flags=re.M)[0]


def check_skill(skill_dir: Path) -> list[str]:
    p: list[str] = []
    skill_md = skill_dir / "SKILL.md"
    text = skill_md.read_text()
    fm, body = parse_frontmatter(text)
    name, desc = fm.get("name", ""), fm.get("description", "")

    if not re.fullmatch(rf"[a-z0-9-]{{1,{MAX_NAME}}}", name):
        p.append(f"name {name!r} must be 1-{MAX_NAME} lowercase letters, digits, hyphens")
    if name != skill_dir.name:
        p.append(f"name {name!r} must match directory {skill_dir.name!r}")
    if any(r in name for r in RESERVED):
        p.append(f"name {name!r} contains a reserved word")
    if not desc:
        p.append("description is empty")
    if len(desc) > MAX_DESC:
        p.append(f"description is {len(desc)} chars (max {MAX_DESC}, i.e. 1024)")
    if desc and not _third_person(desc):
        p.append("description must be written in third person (e.g. 'Finds…', not 'Find…')")
    if ": " in desc:
        p.append("description contains ': ' which breaks the YAML scalar — use an em dash")
    if "<" in desc and ">" in desc:
        p.append("description must not contain XML tags")

    body_lines = body.splitlines()
    # Agents often cd into the skill to run scripts and then write outputs there, polluting
    # the installed plugin; every skill must say where files go.
    if "never inside the skill directory" not in " ".join(body.split()):
        p.append(
            "SKILL.md must tell the agent to write files in the user's working directory, "
            "'never inside the skill directory'"
        )
    if len(body_lines) >= MAX_BODY_LINES:
        p.append(
            f"SKILL.md body is {len(body_lines)} lines (keep under {MAX_BODY_LINES}, i.e. 500)"
        )
    head = "\n".join(body_lines[:15])
    if not re.search(r"^## Contents\s*$", head, re.M):
        p.append(
            "SKILL.md must start with a '## Contents' table of contents (within first 15 lines)"
        )

    refs_dir = skill_dir / "references"
    refs = sorted(r.name for r in refs_dir.glob("*.md")) if refs_dir.exists() else []
    linked = set(REF_LINK.findall(body))
    for r in refs:
        if r not in linked:
            p.append(f"references/{r} is not linked from SKILL.md")
        rtext = (refs_dir / r).read_text()
        if REF_LINK.search(rtext):
            p.append(f"references/{r} links other references — keep references one level deep")
        if len(rtext.splitlines()) > REF_TOC_LINES and not re.search(
            r"^## Contents\s*$", rtext, re.M
        ):
            p.append(f"references/{r} is over {REF_TOC_LINES} lines and needs a table of contents")
    for missing in linked - set(refs):
        p.append(f"SKILL.md links references/{missing} which does not exist")

    for f in [skill_md, *(refs_dir / r for r in refs)]:
        t = f.read_text()
        if BACKSLASH_PATH.search(t):
            p.append(f"{f.name} uses a backslash path — use forward slashes")
        m = TIME_SENSITIVE.search(_strip_old_patterns(t))
        if m:
            p.append(
                f"{f.name} has time-sensitive wording {m.group(0)!r} — move it to '## Old patterns'"
            )
    return p


def main() -> int:
    bad = 0
    for d in sorted(SKILLS.iterdir()):
        if not (d / "SKILL.md").exists():
            continue
        problems = check_skill(d)
        for pr in problems:
            print(f"{d.name}: {pr}")
        bad += len(problems)
    print("ok" if not bad else f"{bad} problem(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
