# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json

import run_evals as re_
from conftest import ROOT

CLAUDE_STREAM = "\n".join(
    json.dumps(x)
    for x in [
        {"type": "system", "subtype": "init", "skills": ["ecmwf-weather:open-data"]},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "ecmwf-weather:open-data"},
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Bash",
                        "input": {
                            "command": (
                                "python3 /x/skills/open-data/scripts/odcatalog.py latest --json"
                            )
                        },
                    }
                ]
            },
        },
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Read",
                        "input": {"file_path": "/x/skills/earthkit/SKILL.md"},
                    }
                ]
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "result": "Latest ECMWF IFS run is 06 UTC. Data: © 2026 ECMWF, CC BY 4.0",
            "total_cost_usd": 0.12,
        },
    ]
)

CODEX_STREAM = "\n".join(
    json.dumps(x)
    for x in [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "bash -lc 'cat .agents/skills/open-data/SKILL.md'",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": "uv run .agents/skills/open-data/scripts/odpoint.py --lat 1 --lon 2",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": "It will be 20 °C. Data: © ECMWF",
            },
        },
    ]
)


def test_parse_claude_stream():
    t = re_.parse_transcript("claude", CLAUDE_STREAM)
    assert t["skills"] == {"open-data", "earthkit"}
    assert any("odcatalog.py latest" in c for c in t["commands"])
    assert t["final"].startswith("Latest ECMWF")
    assert t["cost_usd"] == 0.12


def test_parse_codex_stream():
    t = re_.parse_transcript("codex", CODEX_STREAM)
    assert t["skills"] == {"open-data"}
    assert any("odpoint.py" in c for c in t["commands"])
    assert "20 °C" in t["final"]


def test_agent_error_is_reported_not_graded():
    raw = json.dumps(
        {
            "type": "result",
            "is_error": True,
            "terminal_reason": "api_error",
            "result": "Failed to authenticate: OAuth session expired",
        }
    )
    t = re_.parse_transcript("claude", raw)
    assert t["error"] and "authenticate" in t["error"]
    assert re_.parse_transcript("claude", CLAUDE_STREAM)["error"] is None


def test_parse_gemini_document_and_error():
    ok = json.dumps(
        {
            "response": "Run 06 UTC",
            "stats": {"tools": {"byName": {"run_shell_command": {"count": 1}}}},
        },
        indent=2,
    )
    t = re_.parse_transcript("gemini", ok)
    assert t["final"] == "Run 06 UTC" and t["error"] is None
    bad = "YOLO mode is enabled.\n" + json.dumps(
        {"error": {"message": "must specify GEMINI_API_KEY"}}, indent=2
    )
    assert "GEMINI_API_KEY" in re_.parse_transcript("gemini", bad)["error"]


def test_grade_all_kinds():
    t = re_.parse_transcript("claude", CLAUDE_STREAM)
    results = re_.grade(
        t,
        [
            {"kind": "skill", "name": "open-data"},
            {"kind": "command", "pattern": r"odcatalog\.py"},
            {"kind": "final", "pattern": r"(?i)ecmwf"},
            {"kind": "final_not", "pattern": "scda"},
            {"kind": "skill", "name": "mars"},
            {"kind": "command_not", "pattern": r"pip install earthkit\b"},
        ],
    )
    assert [r["ok"] for r in results] == [True, True, True, True, False, True]


def test_command_match_ignores_shell_quotes():
    t = {
        "skills": set(),
        "commands": ['python3 "$SKILL_DIR/scripts/cds.py" check'],
        "final": "",
        "error": None,
    }
    assert re_.grade(t, [{"kind": "command", "pattern": r"cds\.py\s+check"}])[0]["ok"]


def test_summary_line_counts_errors_separately():
    rs = [
        {"passed": True, "error": None},
        {"passed": False, "error": "auth"},
        {"passed": False, "error": None},
    ]
    assert re_.summary_line(rs) == "1/3 passed, 1 failed, 1 error (agent did not run)"


def test_load_cases_valid():
    cases = re_.load_cases(ROOT / "evals" / "cases")
    assert cases, "no eval cases"
    for c in cases:
        assert {"id", "prompt", "expect"} <= set(c)
        assert all(e["kind"] in re_.KINDS for e in c["expect"])


def test_claude_command_isolated(tmp_path):
    cmd = re_.agent_command("claude", "hello", tmp_path, {"max_budget_usd": 1})
    assert cmd[0] == "claude" and "-p" in cmd
    assert "--plugin-dir" in cmd and str(re_.PLUGIN) in cmd
    assert cmd[cmd.index("--setting-sources") + 1] == "project"


def test_claude_command_bare_in_ci(tmp_path, monkeypatch):
    monkeypatch.setenv("EVAL_BARE", "1")
    cmd = re_.agent_command("claude", "hello", tmp_path, {})
    assert "--bare" in cmd
    monkeypatch.delenv("EVAL_BARE")
    assert "--bare" not in re_.agent_command("claude", "hello", tmp_path, {})
