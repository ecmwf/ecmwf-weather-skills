# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json

import run_evals as re_
from conftest import ROOT

CLAUDE_STREAM = "\n".join(
    json.dumps(x)
    for x in [
        {"type": "system", "subtype": "init", "skills": ["ecmwf-weather:ecmwf-open-data"]},
        {
            "type": "assistant",
            "message": {
                "content": [
                    {
                        "type": "tool_use",
                        "name": "Skill",
                        "input": {"skill": "ecmwf-weather:ecmwf-open-data"},
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
                                "python3 /x/skills/ecmwf-open-data/scripts/odcatalog.py "
                                "latest --json"
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
                        "input": {"file_path": "/x/skills/ecmwf-earthkit/SKILL.md"},
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
                "command": "bash -lc 'cat .agents/skills/ecmwf-open-data/SKILL.md'",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "command": (
                    "uv run .agents/skills/ecmwf-open-data/scripts/odpoint.py --lat 1 --lon 2"
                ),
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
    assert t["skills"] == {"ecmwf-open-data", "ecmwf-earthkit"}
    assert any("odcatalog.py latest" in c for c in t["commands"])
    assert t["final"].startswith("Latest ECMWF")
    assert t["cost_usd"] == 0.12


def test_parse_codex_stream():
    t = re_.parse_transcript("codex", CODEX_STREAM)
    assert t["skills"] == {"ecmwf-open-data"}
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
            {"kind": "skill", "name": "ecmwf-open-data"},
            {"kind": "command", "pattern": r"odcatalog\.py"},
            {"kind": "final", "pattern": r"(?i)ecmwf"},
            {"kind": "final_not", "pattern": "scda"},
            {"kind": "skill", "name": "ecmwf-mars"},
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


def test_command_match_sees_through_shell_variables():
    cmd = (
        "cat > r.mars <<EOF\nretrieve\nEOF\n"
        "S=/x/skills/ecmwf-mars/scripts/mars.py; python3 $S lint r.mars"
    )
    cmd2 = 'S="/x/skills/ecmwf-mars/scripts/mars.py"\npython3 "${S}" plan r.mars'
    for c in (cmd, cmd2):
        t = {"skills": set(), "commands": [c], "final": "", "error": None}
        assert re_.grade(t, [{"kind": "command", "pattern": r"mars\.py\s+(lint|plan)"}])[0]["ok"], c


def test_expand_shell_vars_leaves_unknown_variables():
    assert re_.expand_shell_vars("echo $HOME") == "echo $HOME"


def test_each_case_uses_a_private_copy_of_the_plugin(tmp_path):
    copy = re_.plugin_copy(tmp_path)
    assert (copy / "skills" / "ecmwf-open-data" / "SKILL.md").exists()
    assert re_.PLUGIN not in copy.parents and copy != re_.PLUGIN
    assert not any(p.name == "__pycache__" for p in copy.rglob("*"))
    cmd = re_.agent_command("claude", "hi", tmp_path, {}, plugin=copy)
    assert cmd[cmd.index("--plugin-dir") + 1] == str(copy)


def test_image_size_expectation(tmp_path):
    import struct
    import zlib

    def png(w, h):
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
        chunk = b"IHDR" + ihdr
        return (
            b"\x89PNG\r\n\x1a\n"
            + struct.pack(">I", 13)
            + chunk
            + struct.pack(">I", zlib.crc32(chunk))
        )

    (tmp_path / "ok.png").write_bytes(png(1024, 768))
    (tmp_path / "bad.png").write_bytes(png(1023, 767))
    t = {"skills": set(), "commands": [], "final": "", "error": None}
    r = re_.grade(
        t,
        [
            {"kind": "image_size", "pattern": "ok.png", "size": [1024, 768]},
            {"kind": "image_size", "pattern": "bad.png", "size": [1024, 768]},
            {"kind": "image_size", "pattern": "missing.png", "size": [1, 1]},
        ],
        tmp_path,
    )
    assert [x["ok"] for x in r] == [True, False, False]


def test_case_env_overrides_are_applied():
    case = {"setup": {"env": {"CDSAPI_RC": "/nonexistent/.cdsapirc"}}}
    env = re_.case_env(case, base={"HOME": "/h", "CDSAPI_RC": "/real"})
    assert env["CDSAPI_RC"] == "/nonexistent/.cdsapirc" and env["HOME"] == "/h"
    assert re_.case_env({}, base={"A": "1"}) == {"A": "1"}


def test_run_dirs_are_unique_within_the_same_second(tmp_path):
    a = re_.new_run_dir(tmp_path, stamp="20261004-020916")
    b = re_.new_run_dir(tmp_path, stamp="20261004-020916")
    assert a != b and a.is_dir() and b.is_dir()
