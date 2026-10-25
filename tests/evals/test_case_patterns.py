# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Pin the patterns of the earthkit eval cases that grade upstream reports and figure code.

Each pattern is run through the harness's own `grade`, so shell-quote stripping and the
final/command split are the ones a real run uses.
"""

import json

import pytest
import run_evals as re_
from conftest import ROOT

CASES = ROOT / "evals" / "cases"


def expectation(case_id: str, kind: str, marker: str) -> dict:
    """The one expectation of `kind` in the case whose pattern contains `marker`."""
    case = json.loads((CASES / f"{case_id}.json").read_text(encoding="utf-8"))
    found = [e for e in case["expect"] if e["kind"] == kind and marker in e["pattern"]]
    assert len(found) == 1, f"{case_id}: expected one {kind} containing {marker!r}"
    return found[0]


def passes(e: dict, text: str) -> bool:
    """Whether the expectation holds for an agent that wrote `text` (answer, or code to run)."""
    t = {"skills": set(), "commands": [text], "code": "", "final": text, "error": None}
    return re_.grade(t, [e])[0]["ok"]


SIZE_CASES = ["ek-humidity", "ek-multi-panel"]

SIZE_FOUND = [
    "ekp.Figure(size=(12, 8))",
    "ekp.Figure(rows=1, columns=2, size=(12, 4.5))",
    "Figure(rows=1, columns=2, domain=foo(), size=(9, 3))",
    'fig = ekp.Figure(rows=1, columns=2, size=(12, 4), domain="Europe")',
]
SIZE_ABSENT = [
    "ekp.Figure(figsize=(12, 8))",
    "ekp.Figure(rows=2, columns=1)",
    "ekp.Figure(rows=1, columns=2, domain=foo(), figsize=(9, 3))",
    "fig.size = 3",
    "ekp.Map(domain='Europe'); size = 3",
]


@pytest.mark.parametrize("case_id", SIZE_CASES)
def test_deprecated_size_argument_of_figure_is_rejected(case_id):
    e = expectation(case_id, "command_not", "Figure")
    # command_not passes when the pattern is absent
    for code in SIZE_FOUND:
        assert not passes(e, code), code
    for code in SIZE_ABSENT:
        assert passes(e, code), code


def test_multi_panel_case_needs_a_figure_and_a_png():
    case = json.loads((CASES / "ek-multi-panel.json").read_text(encoding="utf-8"))
    assert {"kind": "skill", "name": "ecmwf-earthkit"} in case["expect"]
    assert {"kind": "file", "pattern": "panels.png"} in case["expect"]
    figure = expectation("ek-multi-panel", "command", r"\bFigure\(")
    assert passes(figure, "fig = ekp.Figure(rows=1, columns=2, figsize=(12, 5))")
    assert not passes(figure, "ekp.Map(domain='Europe')")
    # hand-written matplotlib subplots do not count as the earthkit route
    drawn = expectation("ek-multi-panel", "command_not", r"plt\.subplots")
    assert not passes(drawn, "fig, axes = plt.subplots(1, 2)")
    assert passes(drawn, "fig = ekp.Figure(rows=1, columns=2, figsize=(12, 5))")


def test_multi_panel_input_is_a_fixture_with_both_fields():
    case = json.loads((CASES / "ek-multi-panel.json").read_text(encoding="utf-8"))
    ((source, target),) = case["setup"]["copy"].items()
    assert (ROOT / source).is_file()
    assert target == "forecast.grib2" and "forecast.grib2" in case["prompt"]


REPORT_FOUND = [
    "already reported as https://github.com/ecmwf/earthkit-utils/issues/88",
    "This is earthkit-utils #88",
    "This is earthkit-utils#88",
    "see issue #88",
    "See Issue # 88 upstream.",
]
REPORT_ABSENT = [
    "issue 8",
    "see issue #8 and #880",
    "#880",
    "see issue #880",
    "https://github.com/ecmwf/earthkit-utils/issues/880",
    "earthkit-meteo #214",
    "I can draft a new issue for earthkit-utils",
]


def test_upstream_bug_case_names_the_existing_report():
    e = expectation("ek-upstream-bug", "final", "88")
    for text in REPORT_FOUND:
        assert passes(e, text), text
    for text in REPORT_ABSENT:
        assert not passes(e, text), text


COMMENT_FOUND = [
    "Shall I add a comment with your case to that issue?",
    "I can post a comment on it",
    "Would you like me to comment on the issue?",
    "Want me to leave a comment there with your example?",
    "If you like, I can write a short comment to that issue.",
    "I could comment on this report with your file.",
]
COMMENT_ABSENT = [
    "I commented out the line",
    "The code has a comment about units.",
    "Shall I report it upstream?",
    "Would you like me to open a new issue?",
]


def test_upstream_bug_case_offers_a_comment_on_the_existing_report():
    e = expectation("ek-upstream-bug", "final", "comment")
    for text in COMMENT_FOUND:
        assert passes(e, text), text
    for text in COMMENT_ABSENT:
        assert not passes(e, text), text


def test_upstream_bug_case_keeps_its_original_expectations():
    case = json.loads((CASES / "ek-upstream-bug.json").read_text(encoding="utf-8"))
    kept = [
        {"kind": "skill", "name": "ecmwf-earthkit"},
        {"kind": "final", "pattern": "(?i)(xarray|units\\s*=)"},
        {"kind": "final", "pattern": "(?i)(issue|report)"},
        {"kind": "final", "pattern": "(?i)\\?|if you (want|like)|shall I|would you like"},
        {"kind": "command_not", "pattern": "gh\\s+(issue|pr)\\s+create"},
    ]
    assert all(k in case["expect"] for k in kept)
