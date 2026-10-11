# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Every product in ECMWF's Real-time Catalogue is CC BY 4.0 with no information cost (ECMWF
Open Data Roadmap): beyond the free Open Data, what is restricted is access — an entitled ECMWF
account or a Real-time service agreement — not the licence. No README, skill text, reference,
script or research note may say otherwise."""

import re

import pytest
from conftest import ROOT, SKILLS

OUTDATED = re.compile(
    r"(?i)organisation's ECMWF licen[cs]e"
    r"|ECMWF licen[cs]e for operational"
    r"|operational data (?:needs|requires|under|is used under) an? ECMWF licen[cs]e"
    r"|licensed per account"
    r"|research licen[cs]e"
    r"|without an ECMWF licen[cs]e"
    r"|licensed (?:users|account|ECMWF data)|\(licensed\)"
    r"|operational data isn't CC BY"
    r"|governed by the user's licen[cs]e"
)
FILES = sorted(
    [
        ROOT / "README.md",
        ROOT / "plugins" / "ecmwf-weather" / "README.md",
        *SKILLS.glob("*/SKILL.md"),
        *SKILLS.glob("*/references/*.md"),
        *SKILLS.glob("*/scripts/*.py"),
        *(ROOT / "docs" / "research").glob("*.md"),
    ]
)


def test_files_are_found():
    assert len(FILES) > 20


@pytest.mark.parametrize("path", FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_no_outdated_licence_claim(path):
    text = " ".join(path.read_text().split())  # a claim may wrap across lines
    m = OUTDATED.search(text)
    assert m is None, (
        f"{path.relative_to(ROOT)} says {m.group(0)!r}: ECMWF real-time data is CC BY 4.0 — "
        "say what restricts access instead"
    )
