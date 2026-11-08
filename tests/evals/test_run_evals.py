# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

import pytest
import run_evals as re_
from conftest import ROOT


def a_scratch(tmp_path):
    """The per-case folder the environment points at (an absent gh, an empty gh configuration)."""
    return re_.make_scratch(tmp_path / "scratch", tmp_path / "gh-absent.jsonl")


def a_sandbox(tmp_path):
    """A Sandbox whose command line is built but never run: tests replace subprocess.run."""
    return re_.Sandbox("bwrap", tmp_path / "home", tmp_path / "hosts", {})


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


def test_case_env_overrides_are_applied(tmp_path):
    case = {"setup": {"env": {"CDSAPI_RC": "/nonexistent/.cdsapirc"}}}
    scratch = a_scratch(tmp_path)
    env = re_.case_env(case, scratch, base={"HOME": "/h", "CDSAPI_RC": "/real"})
    assert env["CDSAPI_RC"] == "/nonexistent/.cdsapirc" and env["HOME"] == "/h"
    # a variable outside the allow list does not reach the agent
    assert "A" not in re_.case_env({}, scratch, base={"A": "1"})


def test_run_dirs_are_unique_within_the_same_second(tmp_path):
    a = re_.new_run_dir(tmp_path, stamp="20261004-020916")
    b = re_.new_run_dir(tmp_path, stamp="20261004-020916")
    assert a != b and a.is_dir() and b.is_dir()


OPENCODE_STREAM = "\n".join(
    json.dumps(x)
    for x in [
        {"type": "step_start", "part": {"type": "step-start"}},
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "skill",
                "state": {"input": {"name": "ecmwf-open-data"}},
            },
        },
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "bash",
                "state": {
                    "input": {
                        "command": "python3 scripts/odcatalog.py latest --json",
                        "workdir": "/p/.agents/skills/ecmwf-open-data",
                    }
                },
            },
        },
        {"type": "text", "part": {"type": "text", "text": "Thinking..."}},
        {"type": "text", "part": {"type": "text", "text": "Latest run 00 UTC. Data: © ECMWF"}},
    ]
)

PI_STREAM = "\n".join(
    json.dumps(x)
    for x in [
        {
            "type": "tool_execution_start",
            "toolName": "read",
            "args": {"path": "/x/skills/ecmwf-mars/SKILL.md"},
        },
        {
            "type": "tool_execution_start",
            "toolName": "bash",
            "args": {"command": "python3 /x/skills/ecmwf-mars/scripts/mars.py lint r.mars"},
        },
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "class=od, lint clean"}],
            },
        },
    ]
)


def test_parse_opencode_stream():
    t = re_.parse_transcript("opencode", OPENCODE_STREAM)
    assert t["skills"] == {"ecmwf-open-data"}
    assert any("odcatalog.py latest" in c for c in t["commands"])
    assert t["final"].startswith("Latest run") and t["error"] is None


def test_parse_opencode_error():
    raw = json.dumps(
        {"type": "error", "error": {"name": "ProviderAuthError", "data": {"message": "no key"}}}
    )
    assert "ProviderAuthError" in re_.parse_transcript("opencode", raw)["error"]


def test_parse_pi_stream():
    t = re_.parse_transcript("pi", PI_STREAM)
    assert t["skills"] == {"ecmwf-mars"}
    assert any("mars.py lint" in c for c in t["commands"])
    assert t["final"] == "class=od, lint clean" and t["error"] is None


def test_parse_pi_error():
    raw = json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [],
                "stopReason": "error",
                "errorMessage": "No API key for provider",
            },
        }
    )
    assert "No API key" in re_.parse_transcript("pi", raw)["error"]


def test_opencode_and_pi_commands(tmp_path):
    oc = re_.agent_command("opencode", "hi", tmp_path, {}, model="anthropic/claude-opus-4-8")
    assert oc[:2] == ["opencode", "run"] and "--auto" in oc and "--format" in oc
    assert oc[oc.index("--format") + 1] == "json"
    assert oc[oc.index("-m") + 1] == "anthropic/claude-opus-4-8"
    assert oc[-1] == "hi"
    pi = re_.agent_command("pi", "hi", tmp_path, {}, plugin=tmp_path / "plug", model="x/y")
    assert pi[0] == "pi" and pi[pi.index("--mode") + 1] == "json" and "--no-session" in pi
    assert pi[pi.index("--skill") + 1] == str(tmp_path / "plug" / "skills")
    assert pi[pi.index("--model") + 1] == "x/y"


def test_opencode_command_has_only_flags_opencode_2_accepts(tmp_path):
    # opencode 2 rejects --dir ("Unrecognized flag"); it works in the directory it starts in.
    oc = re_.agent_command("opencode", "hi", tmp_path, {}, model="a/b")
    assert "--dir" not in oc
    # a private server: nothing of a shared background service reaches the case
    assert "--standalone" in oc
    assert {a for a in oc if a.startswith("--")} == {"--format", "--auto", "--standalone"}


def test_opencode_runs_in_the_case_workdir(tmp_path, monkeypatch):
    seen = {}
    stdout = (ROOT / "tests" / "fixtures" / "opencode-2-run.jsonl").read_text()

    def fake_run(cmd, **kw):
        seen["cwd"] = kw["cwd"]
        seen["pwd"] = kw["env"].get("PWD")
        return re_.subprocess.CompletedProcess(cmd, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(re_.subprocess, "run", fake_run)
    out = tmp_path / "out"
    out.mkdir()
    res = re_.run_case(
        "opencode",
        {"id": "c", "prompt": "hi", "expect": []},
        out,
        None,
        1,
        sandbox=a_sandbox(tmp_path),
    )
    assert str(seen["cwd"]).startswith("/") and "ecmwf-eval-c-" in str(seen["cwd"])
    assert res["workdir"] == str(seen["cwd"])
    # opencode takes its directory from $PWD, which the launching shell leaves at the repository
    assert seen["pwd"] == str(seen["cwd"])
    # the summary keeps what the agent ran and which skills it used
    assert any("opencharts.py get" in c for c in res["commands"])
    assert "ecmwf-opencharts-wms" in res["skills"]


def test_parse_opencode_2_stream_records_shell_commands_skills_and_mcp_calls():
    raw = (ROOT / "tests" / "fixtures" / "opencode-2-run.jsonl").read_text()
    t = re_.parse_transcript("opencode", raw, mcp_servers=("ecmwf",))
    assert t["commands"] == [
        "cd /tmp/ecmwf-eval-x && python3 .agents/skills/ecmwf-opencharts-wms/scripts/"
        "opencharts.py get medium-2t-wind --step 48 -o chart.png"
    ]
    assert t["skills"] == {"ecmwf-opencharts-wms", "ecmwf-destine"}  # skill tool id + SKILL.md read
    assert t["mcp"] == ["ecmwf:charts_get_meteogram"]
    assert t["final"].startswith("The chart is saved") and t["error"] is None
    ok = re_.grade(
        t,
        [
            {"kind": "skill", "name": "ecmwf-opencharts-wms"},
            {"kind": "command", "pattern": r"opencharts\.py\s+get"},
            {"kind": "mcp", "pattern": r"^ecmwf:charts_get_meteogram$"},
        ],
    )
    assert all(r["ok"] for r in ok)


def test_opencode_skill_tool_without_a_name_records_no_empty_skill():
    raw = json.dumps(
        {"type": "tool_use", "part": {"type": "tool", "tool": "skill", "state": {"input": {}}}}
    )
    assert re_.parse_transcript("opencode", raw)["skills"] == set()


def test_opencode_workdir_gets_project_skills(tmp_path):
    plugin = re_.plugin_copy(tmp_path)
    wd = re_.prepare_workdir("opencode", {"id": "c"}, plugin)
    assert (wd / ".agents" / "skills" / "ecmwf-open-data" / "SKILL.md").exists()


def test_opencode_commands_include_their_workdir():
    t = re_.parse_transcript("opencode", OPENCODE_STREAM)
    cmd = next(c for c in t["commands"] if "odcatalog" in c)
    assert cmd.startswith("cd /p/.agents/skills/ecmwf-open-data && ")
    ok = re_.grade(t, [{"kind": "command", "pattern": r"skills/ecmwf-open-data\b.*odcatalog"}])
    assert ok[0]["ok"]


def test_agent_that_exits_without_doing_anything_is_an_error():
    t = {"skills": set(), "commands": [], "final": "", "error": None}
    re_.note_startup_failure(t, 1, "No API key found for the selected model.\\nUse /login")
    assert "No API key" in t["error"]
    worked = {"skills": set(), "commands": ["ls"], "final": "", "error": None}
    re_.note_startup_failure(worked, 1, "boom")
    assert worked["error"] is None


def test_code_written_to_files_is_graded():
    claude = "\n".join(
        json.dumps(x)
        for x in [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Write",
                            "input": {
                                "file_path": "/w/rh.py",
                                "content": "from earthkit.meteo import thermo\n",
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
                            "name": "Edit",
                            "input": {
                                "file_path": "/w/rh.py",
                                "old_string": "x",
                                "new_string": "t - 273.15",
                            },
                        }
                    ]
                },
            },
        ]
    )
    t = re_.parse_transcript("claude", claude)
    assert "earthkit.meteo" in t["code"] and "273.15" in t["code"]
    r = re_.grade(
        t,
        [
            {"kind": "command", "pattern": r"earthkit\.meteo"},
            {"kind": "command_not", "pattern": r"273\.15"},
        ],
    )
    assert [x["ok"] for x in r] == [True, False]


def test_codex_patches_are_read_from_its_session_log(tmp_path):
    wd = tmp_path / "work"
    log = tmp_path / "sessions" / "2026" / "10" / "08" / "rollout-1.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text(
        "\n".join(
            json.dumps(x)
            for x in [
                {"type": "session_meta", "payload": {"cwd": str(wd)}},
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "input": (
                            'const patch = "*** Begin Patch\\n*** Add File: m.py\\n'
                            '+from earthkit.transforms import ensemble\\n";'
                        ),
                    },
                },
                {
                    "type": "response_item",
                    "payload": {
                        "type": "custom_tool_call",
                        "name": "exec",
                        "input": 'const r = await tools.exec_command({cmd:"ls"});',
                    },
                },
            ]
        )
    )
    code = re_.codex_written_code(wd, sessions=tmp_path / "sessions")
    assert "earthkit.transforms" in code and "exec_command" not in code
    assert re_.codex_written_code(tmp_path / "other", sessions=tmp_path / "sessions") == ""


def test_opencode_write_tool_content_is_code():
    raw = json.dumps(
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "write",
                "state": {
                    "input": {"filePath": "/w/a.py", "content": "import earthkit.plots as ekp\n"}
                },
            },
        }
    )
    assert "earthkit.plots" in re_.parse_transcript("opencode", raw)["code"]


def test_case_cleanup_removes_skill_copies_but_keeps_outputs(tmp_path):
    plugin = re_.plugin_copy(tmp_path / "plugin")
    wd = re_.prepare_workdir("opencode", {"id": "c"}, plugin)
    (wd / "out.png").write_bytes(b"x")
    re_.cleanup_case(plugin, wd)
    assert not plugin.exists() and not (wd / ".agents").exists() and (wd / "out.png").exists()


def test_skills_are_real_directories_that_find_can_walk(tmp_path):
    # `find .agents/skills -type f -name SKILL.md` does not descend into symlinked directories;
    # an agent that searches this way must still find the skills.
    plugin = re_.plugin_copy(tmp_path / "plugin")
    wd = re_.prepare_workdir("codex", {"id": "c"}, plugin)
    skills = wd / ".agents" / "skills"
    assert not any(p.is_symlink() for p in skills.iterdir())
    found = [p for p in skills.rglob("SKILL.md")]
    assert len(found) == len(list((plugin / "skills").glob("*/SKILL.md")))


# --- the ECMWF MCP server in evals --------------------------------------------------------------


def _jsonl(events):
    return "\n".join(json.dumps(x) for x in events)


def _claude_tool(name, inp):
    return {
        "type": "assistant",
        "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]},
    }


CLAUDE_MCP = _jsonl(
    [
        _claude_tool(
            "mcp__plugin_ecmwf-weather_ecmwf__forecast_get_point_forecast",
            {"collection_id": "operational-data", "latitude": 51.45, "longitude": -0.97},
        ),
        _claude_tool("WebSearch", {"query": "Reading weather tomorrow"}),
        _claude_tool("WebFetch", {"url": "https://www.example.org/forecast", "prompt": "rain?"}),
        {"type": "result", "result": "Dry, 15 °C. Data: ECMWF", "total_cost_usd": 0.05},
    ]
)

CODEX_MCP = _jsonl(
    [
        {
            "type": "item.started",
            "item": {"type": "mcp_tool_call", "server": "ecmwf", "tool": "find_service"},
        },
        {
            "type": "item.completed",
            "item": {
                "type": "mcp_tool_call",
                "server": "ecmwf",
                "tool": "find_service",
                "arguments": {"question": "rain in Bologna"},
                "status": "completed",
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "web_search",
                "query": "Bologna meteo",
                "action": {"type": "search", "queries": ["Bologna weather forecast mm"]},
            },
        },
        {
            "type": "item.completed",
            "item": {
                "type": "web_search",
                "query": "",
                "action": {"type": "open_page", "url": "https://www.14cast.com/italy/bologna"},
            },
        },
        {"type": "item.completed", "item": {"type": "agent_message", "text": "About 1.5 mm."}},
    ]
)

OPENCODE_MCP = _jsonl(
    [
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "ecmwf_charts_get_meteogram",
                "state": {"input": {"latitude": 51.4, "longitude": -1.32}},
            },
        },
        {
            "type": "tool_use",
            "part": {
                "type": "tool",
                "tool": "webfetch",
                "state": {"input": {"url": "https://www.ipma.pt/en/otempo/prev-sam/"}},
            },
        },
        {
            "type": "tool_use",
            "part": {"type": "tool", "tool": "websearch", "state": {"input": {"query": "Lisbon"}}},
        },
        {"type": "text", "part": {"type": "text", "text": "Saved newbury.png"}},
    ]
)

PI_MCP = _jsonl(
    [
        {
            "type": "tool_execution_start",
            "toolName": "mcp__ecmwf__reanalysis_get_point_history",
            "args": {"latitude": 48.86, "longitude": 2.35},
        },
        {
            "type": "tool_execution_start",
            "toolName": "codemode",
            "args": {"code": "await mcp__ecmwf__charts_get_chart({product: 'x'})"},
        },
    ]
)


def test_claude_mcp_calls_and_web_use_are_recorded():
    t = re_.parse_transcript("claude", CLAUDE_MCP)
    assert t["mcp"] == ["plugin_ecmwf-weather_ecmwf:forecast_get_point_forecast"]
    assert t["web"] == [
        "WebSearch: Reading weather tomorrow",
        "WebFetch: https://www.example.org/forecast",
    ]


def test_codex_mcp_calls_and_web_searches_are_recorded():
    t = re_.parse_transcript("codex", CODEX_MCP)
    assert t["mcp"] == ["ecmwf:find_service"]  # started + completed is one call
    assert len(t["web"]) == 2
    assert "Bologna weather forecast mm" in t["web"][0]
    assert "14cast.com" in t["web"][1]


def test_opencode_mcp_calls_and_web_use_are_recorded():
    t = re_.parse_transcript("opencode", OPENCODE_MCP, mcp_servers=("ecmwf",))
    assert t["mcp"] == ["ecmwf:charts_get_meteogram"]
    assert t["web"] == ["webfetch: https://www.ipma.pt/en/otempo/prev-sam/", "websearch: Lisbon"]


def test_pi_mcp_calls_are_recorded_direct_and_through_codemode():
    t = re_.parse_transcript("pi", PI_MCP)
    assert t["mcp"] == ["ecmwf:reanalysis_get_point_history", "ecmwf:charts_get_chart"]


def test_gemini_web_tools_are_recorded_from_its_stats():
    doc = {"response": "ok", "stats": {"tools": {"byName": {"google_web_search": {"count": 2}}}}}
    assert re_.parse_transcript("gemini", json.dumps(doc))["web"] == ["google_web_search"]


def test_calls_through_the_mcp_script_count_as_mcp_calls():
    t = {
        "skills": set(),
        "commands": [
            "M=/x/skills/ecmwf-open-data/scripts/ecmwf_mcp.py; python3 $M status",
            'python3 "$M" call forecast_get_point_forecast \'{"latitude": 1}\'',
        ],
        "final": "",
        "error": None,
        "mcp": [],
        "web": [],
    }
    assert re_.mcp_calls(t) == ["ecmwf_mcp.py:forecast_get_point_forecast"]
    ok = re_.grade(t, [{"kind": "mcp", "pattern": r"forecast_get_point_forecast"}])
    assert ok[0]["ok"]


def test_grade_mcp_web_and_any():
    t = re_.parse_transcript("claude", CLAUDE_MCP)
    r = re_.grade(
        t,
        [
            {"kind": "mcp", "pattern": "forecast_get_point_forecast"},
            {"kind": "mcp", "pattern": "charts_"},
            {"kind": "mcp_not", "pattern": "forecast_"},
            {"kind": "mcp_not", "pattern": "catalogue_"},
            {"kind": "web_not"},
            {"kind": "web_not", "pattern": "(?i)weather"},
            {"kind": "web_not", "pattern": "(?i)warning"},
            {
                "kind": "any",
                "of": [
                    {"kind": "command", "pattern": r"ptpoint\.py"},
                    {"kind": "mcp", "pattern": "forecast_get_point_forecast"},
                ],
            },
            {"kind": "any", "of": [{"kind": "command", "pattern": r"odpoint\.py"}]},
        ],
    )
    assert [x["ok"] for x in r] == [True, False, False, True, False, False, True, True, False]


def test_web_not_passes_without_web_use():
    t = re_.parse_transcript("claude", CLAUDE_STREAM)
    assert t["web"] == [] and re_.grade(t, [{"kind": "web_not"}])[0]["ok"]


def test_nested_expectations_are_valid_kinds():
    for c in re_.load_cases(ROOT / "evals" / "cases"):
        for e in c["expect"]:
            for sub in e.get("of", []):
                assert sub["kind"] in re_.KINDS - {"any"}, (c["id"], sub)


def test_mcp_servers_come_from_the_plugins_portable_config():
    assert re_.mcp_servers(re_.PLUGIN) == {"ecmwf": "https://mcp.ecmwf.int"}


def test_claude_gets_the_bundled_server_unless_mcp_is_off(tmp_path):
    with_mcp = re_.plugin_copy(tmp_path / "a")
    without = re_.plugin_copy(tmp_path / "b", mcp=False)
    assert (with_mcp / ".mcp.json").exists() and (with_mcp / "mcp.json").exists()
    assert not (without / ".mcp.json").exists() and not (without / "mcp.json").exists()


def test_codex_gets_the_server_on_the_command_line(tmp_path):
    plugin = re_.plugin_copy(tmp_path / "p")
    cmd = re_.agent_command("codex", "hi", tmp_path, {}, plugin=plugin)
    assert cmd[cmd.index("-c") + 1] == 'mcp_servers.ecmwf.url="https://mcp.ecmwf.int"'
    assert cmd[-1] == "hi"
    off = re_.agent_command("codex", "hi", tmp_path, {}, plugin=plugin, mcp=False)
    assert "-c" not in off


def test_opencode_gemini_and_pi_get_the_server_in_their_workdir(tmp_path):
    plugin = re_.plugin_copy(tmp_path / "p")
    oc = re_.prepare_workdir("opencode", {"id": "c"}, plugin)
    assert json.loads((oc / "opencode.json").read_text())["mcp"] == {
        "ecmwf": {"type": "remote", "url": "https://mcp.ecmwf.int", "enabled": True}
    }
    gem = re_.prepare_workdir("gemini", {"id": "c"}, plugin)
    assert json.loads((gem / ".gemini" / "settings.json").read_text())["mcpServers"] == {
        "ecmwf": {"url": "https://mcp.ecmwf.int", "type": "http"}
    }
    pi = re_.prepare_workdir("pi", {"id": "c"}, plugin)
    assert json.loads((pi / ".pi" / "mcp.json").read_text())["mcpServers"] == {
        "ecmwf": {"url": "https://mcp.ecmwf.int"}
    }
    assert "--approve" in re_.agent_command("pi", "hi", pi, {}, plugin=plugin)
    off = re_.prepare_workdir("opencode", {"id": "c"}, plugin, mcp=False)
    assert not (off / "opencode.json").exists()
    assert "--no-mcp" in re_.agent_command("pi", "hi", pi, {}, plugin=plugin, mcp=False)


def test_no_mcp_flag():
    assert re_.parse_args([]).mcp is True
    assert re_.parse_args(["--no-mcp"]).mcp is False


def _case(case_id):
    return next(c for c in re_.load_cases(ROOT / "evals" / "cases") if c["id"] == case_id)


def _graded(case_id, **t):
    base = {"skills": set(), "commands": [], "final": "", "error": None, "mcp": [], "web": []}
    return [r["ok"] for r in re_.grade({**base, **t}, _case(case_id)["expect"])]


def test_mcp_cases_grade_concepts_not_wording():
    import re

    run = _case("mcp-quick-forecast")["expect"][2]["pattern"]
    for ok in (
        "From the ECMWF IFS 00 UTC run of 11 October.",
        "Model run: 2026-10-11T00:00:00Z (ECMWF IFS HRES)",
        "based on the run initialised at 06Z today",
        "the 12 UTC run",
    ):
        assert re.search(run, ok), ok
    for bad in ("Tomorrow will be dry, 15 °C at 12:00.", "Data: ECMWF, run by the server"):
        assert not re.search(run, bad), bad

    web = _case("mcp-quick-forecast")["expect"][0]["pattern"]
    assert re.search(web, "WebSearch: Reading UK weather tomorrow")
    assert re.search(web, "web_search: Bologna meteo 11 12 13 ottobre")
    assert not re.search(web, "WebSearch: Reading UK latitude longitude")
    assert not re.search(web, "web_search: Bologna Italy coordinates")

    used = ["plugin_ecmwf-weather_ecmwf:forecast_get_point_forecast"]
    explained = (
        "ECMWF: about 1.5 mm — tp is accumulated since the run start, so I took the difference."
    )
    assert all(_graded("mcp-rain-accumulation", final=explained, mcp=used))
    assert not all(_graded("mcp-rain-accumulation", final="ECMWF: 20.9 mm in total.", mcp=used))
    script = ["python3 scripts/ecmwf_mcp.py call forecast_get_point_forecast '{}'"]
    assert all(_graded("mcp-rain-accumulation", final="ECMWF: 1.5 mm", commands=script))

    guidance = (
        "ECMWF model output is guidance, not an official warning: check IPMA "
        "(https://www.ipma.pt) or MeteoAlarm (https://meteoalarm.org)."
    )
    assert all(_graded("mcp-official-warning", final=guidance))
    assert not all(_graded("mcp-official-warning", final="No warnings: it will be sunny."))

    valid = _case("mcp-chart-europe")["expect"][1]["pattern"]
    for ok in ("Valid time: Mon 12 Oct 2026 00 UTC", "valid 2026-10-12T00:00Z", "at 12:00 UTC"):
        assert re.search(valid, ok), ok
    assert not re.search(valid, "Saved the chart as mslp.png for tomorrow.")


def test_claude_mcp_server_status_is_recorded():
    init = {
        "type": "system",
        "subtype": "init",
        "mcp_servers": [
            {"name": "plugin:ecmwf-weather:ecmwf", "status": "failed", "source": "plugin"},
            {"name": "claude.ai Google Drive", "status": "connected", "source": "claudeai"},
        ],
    }
    t = re_.parse_transcript("claude", json.dumps(init))
    assert t["mcp_servers"] == {
        "plugin:ecmwf-weather:ecmwf": "failed",
        "claude.ai Google Drive": "connected",
    }
    assert re_.parse_transcript("codex", CODEX_STREAM)["mcp_servers"] == {}


def test_bare_claude_gets_the_bundled_server_explicitly(tmp_path, monkeypatch):
    # --bare skips plugin MCP servers; the plugin's own .mcp.json is passed instead.
    monkeypatch.setenv("EVAL_BARE", "1")
    plugin = re_.plugin_copy(tmp_path / "p")
    cmd = re_.agent_command("claude", "hi", tmp_path, {}, plugin=plugin)
    assert cmd[cmd.index("--mcp-config") + 1] == str(plugin / ".mcp.json")
    off = re_.agent_command("claude", "hi", tmp_path, {}, plugin=plugin, mcp=False)
    assert "--mcp-config" not in off
    monkeypatch.delenv("EVAL_BARE")
    assert "--mcp-config" not in re_.agent_command("claude", "hi", tmp_path, {}, plugin=plugin)


def test_skill_used_through_a_shell_variable_is_detected():
    raw = "\n".join(
        json.dumps(x)
        for x in [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Bash",
                            "input": {
                                "command": (
                                    "S=/tmp/p/ecmwf-weather/skills/ecmwf-polytope/scripts; "
                                    "uv run $S/ptpoint.py --check"
                                )
                            },
                        }
                    ]
                },
            },
        ]
    )
    assert "ecmwf-polytope" in re_.parse_transcript("claude", raw)["skills"]


def test_web_access_to_ecmwf_and_copernicus_sites_is_not_a_web_fallback():
    pattern = r"(?i)\b(weather|temperatures?|heat\w*)\b"

    def web_ok(*entries):
        t = {"commands": [], "final": "", "web": list(entries)}
        return re_.grade(t, [{"kind": "web_not", "pattern": pattern}])[0]["ok"]

    assert web_ok(
        "web_search: site.ecmwf.int Paris August 2003 heatwave temperature | "
        "site.climate.copernicus.eu August 2003 Paris heatwave"
    )
    assert not web_ok("WebSearch: Paris August 2003 heatwave temperature record")
    assert not web_ok(
        "WebFetch: https://www.metoffice.gov.uk/weather/forecast | https://www.ecmwf.int/"
    )


# --- fake JIRA and fake gh ----------------------------------------------------------------------

FAKE_CASE = {
    "id": "fb-fake",
    "prompt": "p",
    "expect": [],
    "setup": {"fakes": {"jira": {}, "gh": {}}},
}


def entries(*lines, kind="read"):
    return [{"line": ln, "kind": kind} for ln in lines]


def graded(expect, jira_log=None, gh_log=None):
    t = {"skills": set(), "commands": [], "final": "", "error": None}
    return re_.grade(t, [expect], jira_log=jira_log, gh_log=gh_log)[0]


@pytest.mark.parametrize("n", [0, 1, 2])
def test_grade_jira_count_is_exact(n):
    log = entries("GET /rest/api/2/serverInfo", *["CREATED key=AS-9001&x=1"] * n)
    for want in (0, 1, 2):
        r = graded({"kind": "jira_count", "pattern": "^CREATED ", "count": want}, jira_log=log)
        assert r["ok"] is (n == want)
        if n != want:
            assert f"{n} matching line(s)" in r["detail"]


@pytest.mark.parametrize("n", [0, 1, 2])
def test_grade_gh_count_is_exact(n):
    log = entries("gh --version", *["gh issue create -R x"] * n, kind="read")
    for want in (0, 1, 2):
        r = graded({"kind": "gh_count", "pattern": "^gh issue create", "count": want}, gh_log=log)
        assert r["ok"] is (n == want)
        if n != want:
            assert f"{n} matching line(s)" in r["detail"]


def test_grade_jira_hit_and_not_hit():
    log = entries('GET /rest/api/2/search?jql=project = "AS"', "POST /rest/api/2/issue")
    assert graded({"kind": "jira_hit", "pattern": r"^POST /rest/api/2/issue"}, jira_log=log)["ok"]
    assert not graded({"kind": "jira_hit", "pattern": "CREATED"}, jira_log=log)["ok"]
    assert graded({"kind": "jira_not_hit", "pattern": "CREATED"}, jira_log=log)["ok"]
    assert not graded({"kind": "jira_not_hit", "pattern": "^POST"}, jira_log=log)["ok"]
    assert graded({"kind": "jira_not_hit", "pattern": "^POST"}, jira_log=[])["ok"]
    assert not graded({"kind": "jira_hit", "pattern": "^POST"}, jira_log=[])["ok"]


def test_grade_gh_hit_and_not_hit():
    log = entries("gh auth status", "gh issue create -R a/b --label skill-feedback", kind="read")
    assert graded(
        {"kind": "gh_hit", "pattern": r"issue create .*--label skill-feedback"}, gh_log=log
    )["ok"]
    assert not graded({"kind": "gh_hit", "pattern": "pr create"}, gh_log=log)["ok"]
    assert graded({"kind": "gh_not_hit", "pattern": "pr create"}, gh_log=log)["ok"]
    assert not graded({"kind": "gh_not_hit", "pattern": "issue create"}, gh_log=log)["ok"]


def test_grade_gh_no_write_looks_at_the_kind_of_every_logged_call():
    reads = entries("gh auth status", "gh api user")
    assert graded({"kind": "gh_no_write"}, gh_log=reads)["ok"]
    assert graded({"kind": "gh_no_write"}, gh_log=[])["ok"]
    for log in (
        [*reads, {"line": "gh issue create -R a/b", "kind": "write", "status": "refused"}],
        [{"line": "gh issue create -R a/b", "kind": "write", "status": "ok"}, *reads],
    ):
        r = graded({"kind": "gh_no_write"}, gh_log=log)
        assert not r["ok"] and "write" in r["detail"]
    # the result line of a created issue is not a call
    done = [{"line": "CREATED number=1&repo=a/b", "kind": "result"}]
    assert graded({"kind": "gh_no_write"}, gh_log=done)["ok"]


def test_grade_fake_kinds_without_a_log_fail_with_a_reason():
    for e, key in (
        ({"kind": "jira_hit", "pattern": "x"}, "jira"),
        ({"kind": "jira_not_hit", "pattern": "x"}, "jira"),
        ({"kind": "jira_count", "pattern": "x", "count": 0}, "jira"),
        ({"kind": "gh_hit", "pattern": "x"}, "gh"),
        ({"kind": "gh_not_hit", "pattern": "x"}, "gh"),
        ({"kind": "gh_count", "pattern": "x", "count": 0}, "gh"),
        ({"kind": "gh_no_write"}, "gh"),
    ):
        r = graded(e)
        assert r["ok"] is False and "log" in r["detail"], e
        # a log of the other fake does not stand in for it
        other = {"gh_log": []} if key == "jira" else {"jira_log": []}
        assert graded(e, **other)["ok"] is False, e


def test_grade_fake_kinds_work_inside_any():
    t = {"skills": set(), "commands": [], "final": "", "error": None}
    e = {
        "kind": "any",
        "of": [{"kind": "jira_hit", "pattern": "^POST"}, {"kind": "gh_hit", "pattern": "x"}],
    }
    assert re_.grade(t, [e], jira_log=entries("POST /rest/api/2/issue"))[0]["ok"]
    assert not re_.grade(t, [e], jira_log=[], gh_log=[])[0]["ok"]


def test_failed_expectation_report_shows_the_reason():
    r = graded({"kind": "jira_count", "pattern": "^CREATED", "count": 1}, jira_log=[])
    assert "0 matching" in re_._failed(r)


def test_the_fake_gh_repository_is_the_one_the_runner_names():
    spec = importlib.util.spec_from_file_location("fake_gh", re_.FAKE_GH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.REPO == re_.GH_REPO


def test_fake_kinds_are_known():
    assert sorted(re_.JIRA_KINDS) == ["jira_count", "jira_hit", "jira_not_hit"]
    assert sorted(re_.GH_KINDS) == ["gh_count", "gh_hit", "gh_no_write", "gh_not_hit"]
    assert (re_.JIRA_KINDS | re_.GH_KINDS) <= re_.KINDS


def case_with(expect, fakes=None):
    case = {"id": "x", "prompt": "p", "expect": expect}
    if fakes is not None:
        case["setup"] = {"fakes": fakes}
    return case


def test_validator_accepts_a_case_with_its_fakes():
    expect = [
        {"kind": "jira_count", "pattern": "^CREATED ", "count": 1},
        {"kind": "jira_hit", "pattern": "x"},
        {"kind": "jira_not_hit", "pattern": "x"},
        {"kind": "gh_count", "pattern": "x", "count": 0},
        {"kind": "gh_hit", "pattern": "x"},
        {"kind": "gh_not_hit", "pattern": "x"},
        {"kind": "gh_no_write"},
        {"kind": "any", "of": [{"kind": "gh_hit", "pattern": "x"}]},
    ]
    fakes = {
        "jira": {"issues": [{"summary": "Old", "labels": ["a"]}]},
        "gh": {"login": "eval-user", "signed_in": True, "access": False, "labels": ["a"]},
    }
    assert re_.case_problems(case_with(expect, fakes)) == []
    assert re_.case_problems(case_with([{"kind": "final", "pattern": "x"}])) == []


def test_validator_requires_the_fake_of_each_kind():
    for e in (
        {"kind": "jira_hit", "pattern": "x"},
        {"kind": "jira_not_hit", "pattern": "x"},
        {"kind": "jira_count", "pattern": "x", "count": 1},
    ):
        assert any("setup.fakes.jira" in p for p in re_.case_problems(case_with([e]))), e
        assert any(
            "setup.fakes.jira" in p for p in re_.case_problems(case_with([e], {"gh": {}}))
        ), e
        assert re_.case_problems(case_with([e], {"jira": {}})) == []
    for e in (
        {"kind": "gh_hit", "pattern": "x"},
        {"kind": "gh_not_hit", "pattern": "x"},
        {"kind": "gh_count", "pattern": "x", "count": 1},
        {"kind": "gh_no_write"},
    ):
        assert any("setup.fakes.gh" in p for p in re_.case_problems(case_with([e]))), e
        assert any(
            "setup.fakes.gh" in p for p in re_.case_problems(case_with([e], {"jira": {}}))
        ), e
        assert re_.case_problems(case_with([e], {"gh": {}})) == []
    nested = {"kind": "any", "of": [{"kind": "gh_hit", "pattern": "x"}]}
    assert any("setup.fakes.gh" in p for p in re_.case_problems(case_with([nested])))


def test_validator_refuses_unknown_fake_keys_and_shapes():
    problems = re_.case_problems(case_with([], {"jira": {}, "svn": {}}))
    assert any("svn" in p for p in problems)
    assert any("fakes" in p for p in re_.case_problems(case_with([], ["jira"])))
    assert any("jira" in p for p in re_.case_problems(case_with([], {"jira": "on"})))
    assert any("nonsense" in p for p in re_.case_problems(case_with([], {"jira": {"nonsense": 1}})))
    assert any("nonsense" in p for p in re_.case_problems(case_with([], {"gh": {"nonsense": 1}})))
    for login in ("a-real-name", "Eval-x", "eval-", 5):
        assert re_.case_problems(case_with([], {"gh": {"login": login}})), login
    assert re_.case_problems(case_with([], {"gh": {"create": "sometimes"}}))
    assert re_.case_problems(case_with([], {"gh": {"signed_in": "yes"}}))
    assert re_.case_problems(case_with([], {"gh": {"issues": [{"title": "no number"}]}}))
    assert re_.case_problems(case_with([], {"jira": {"issues": [{"labels": ["a"]}]}}))


def test_validator_checks_the_fake_expectations():
    fakes = {"jira": {}, "gh": {}}
    bad = [
        {"kind": "jira_count", "pattern": "x"},
        {"kind": "jira_count", "pattern": "x", "count": -1},
        {"kind": "jira_count", "pattern": "x", "count": "1"},
        {"kind": "jira_count", "pattern": "x", "count": True},
        {"kind": "jira_count", "pattern": "x", "count": 1, "extra": 1},
        {"kind": "jira_count", "count": 1},
        {"kind": "jira_hit"},
        {"kind": "jira_hit", "pattern": "("},
        {"kind": "gh_hit", "pattern": ""},
        {"kind": "gh_not_hit", "pattern": "x", "count": 1},
        {"kind": "gh_no_write", "pattern": "x"},
        {"kind": "gh_count", "pattern": "x", "count": 1.5},
        {"kind": "unheard_of"},
    ]
    for e in bad:
        assert re_.case_problems(case_with([e], fakes)), e


def test_load_cases_refuses_an_invalid_case(tmp_path):
    (tmp_path / "ok.json").write_text(json.dumps(case_with([{"kind": "final", "pattern": "x"}])))
    assert [c["id"] for c in re_.load_cases(tmp_path)] == ["x"]
    (tmp_path / "bad.json").write_text(json.dumps(case_with([{"kind": "gh_no_write"}])))
    with pytest.raises(ValueError, match=r"bad\.json.*setup\.fakes\.gh"):
        re_.load_cases(tmp_path)


def test_shipped_cases_have_no_problems():
    for p in sorted((ROOT / "evals" / "cases").glob("*.json")):
        assert re_.case_problems(json.loads(p.read_text())) == [], p.name


def real_gh(env, *args, stdin=None):
    return subprocess.run(
        ["gh", *args], env=env, capture_output=True, text=True, input=stdin, timeout=30
    )


def test_fakes_start_per_case_and_shape_the_environment(tmp_path):
    case = {
        **FAKE_CASE,
        "setup": {
            "fakes": {"jira": {}, "gh": {}},
            "env": {"JIRA_PAT": "from-the-case", "ECMWF_WEATHER_JIRA_URL": "http://elsewhere"},
        },
    }
    fakes = re_.FakeServices(case, tmp_path, "fb-fake.claude.1")
    try:
        fakes.start()
        env = re_.case_env(
            case, a_scratch(tmp_path), base={"PATH": "/usr/bin:/bin", "HOME": "/h"}, fakes=fakes
        )
        assert env["ECMWF_WEATHER_JIRA_URL"] == fakes.jira_url
        assert fakes.jira_url.startswith("http://127.0.0.1:")
        # the synthetic token replaces whatever the case set, and is the fake's own
        assert env["JIRA_PAT"] == re_.fake_jira_token() == "eval-synthetic-jira-token"
        assert env["PATH"].split(os.pathsep)[0] == str(fakes.gh_bin)
        assert env["PATH"].endswith("/usr/bin:/bin") and env["HOME"] == "/h"
        # a `gh` found on PATH is the fake: it answers and logs
        p = real_gh(env, "auth", "status")
        assert p.returncode == 0 and "Logged in" in p.stdout
        p = real_gh(env, "issue", "create", "-R", re_.GH_REPO, "--title", "T", "--body", "b")
        assert p.returncode == 0 and p.stdout.strip().endswith("/issues/1")
        # the fake JIRA answers on that URL with the token
        req = urllib.request.Request(
            env["ECMWF_WEATHER_JIRA_URL"] + "/rest/api/2/serverInfo",
            headers={"Authorization": f"Bearer {env['JIRA_PAT']}"},
        )
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        assert opener.open(req, timeout=10).status == 200
        url = fakes.jira_url
    finally:
        fakes.stop()
    fakes.stop()  # twice is fine
    with pytest.raises(OSError):
        opener.open(url + "/rest/api/2/serverInfo", timeout=2)
    jira_log, gh_log = fakes.read_logs()
    assert [e["line"] for e in jira_log] == ["GET /rest/api/2/serverInfo"]
    assert [e["line"].split(" -R")[0] for e in gh_log if e["kind"] != "result"] == [
        "gh auth status",
        "gh issue create",
    ]
    assert fakes.jira_log == tmp_path / "fb-fake.claude.1.jira.jsonl"
    assert fakes.gh_log == tmp_path / "fb-fake.claude.1.gh.jsonl"
    assert not fakes.gh_bin.exists(), "the shim folder is removed at teardown"


def test_a_gh_only_case_gets_no_jira_settings(tmp_path):
    case = case_with([], {"gh": {"signed_in": False}})
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        assert fakes.jira_url is None and fakes.jira_log is None
        env = re_.case_env(case, a_scratch(tmp_path), base={"PATH": "/bin"}, fakes=fakes)
        # no fake JIRA: no token, and the JIRA host variable is on a closed port
        assert "JIRA_PAT" not in env and env["ECMWF_WEATHER_JIRA_URL"].startswith("http://127.")
        assert env["PATH"].split(os.pathsep)[0] == str(fakes.gh_bin)
        assert real_gh(env, "auth", "status").returncode == 1  # signed out, as configured


def test_a_jira_only_case_has_no_gh_fake_but_an_absent_gh(tmp_path):
    case = case_with([], {"jira": {}})
    scratch = a_scratch(tmp_path)
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        env = re_.case_env(case, scratch, base={"PATH": "/bin"}, fakes=fakes)
        assert env["PATH"] == f"{scratch.gh_bin}{os.pathsep}/bin" and fakes.gh_bin is None
        assert env["JIRA_PAT"] == re_.fake_jira_token()
    assert fakes.read_logs()[1] is None


def test_jira_seed_issues_reach_the_fake(tmp_path):
    case = case_with(
        [], {"jira": {"issues": [{"summary": "Seeded", "labels": ["skill-feedback"]}]}}
    )
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request(
            fakes.jira_url + "/rest/api/2/search?jql=project%20%3D%20%22AS%22",
            headers={"Authorization": f"Bearer {re_.fake_jira_token()}"},
        )
        assert json.loads(opener.open(req, timeout=10).read())["total"] == 1


def test_a_fake_that_does_not_start_is_reported_clearly(tmp_path):
    script = tmp_path / "broken.py"
    script.write_text("import sys\nsys.stderr.write('boom: no port\\n')\nsys.exit(3)\n")
    case = case_with([], {"jira": {}})
    fakes = re_.FakeServices(case, tmp_path, "c", jira_script=script, startup_s=20)
    with pytest.raises(re_.FakeStartError, match=r"fake JIRA did not start.*boom: no port"):
        fakes.start()
    assert not fakes.running()


def test_a_fake_that_is_not_ready_in_time_is_stopped(tmp_path):
    script = tmp_path / "slow.py"
    script.write_text("import time\ntime.sleep(60)\n")
    case = case_with([], {"jira": {}})
    fakes = re_.FakeServices(case, tmp_path, "c", jira_script=script, startup_s=0.5)
    with pytest.raises(re_.FakeStartError, match="fake JIRA did not start"):
        fakes.start()
    assert not fakes.running()


def test_fake_logs_are_read_without_following_links(tmp_path):
    log = tmp_path / "c.jira.jsonl"
    log.write_text(
        json.dumps({"line": "GET /a"}) + "\nnot json\n" + json.dumps({"line": "GET /b"}) + "\n"
    )
    assert [e["line"] for e in re_.read_log(log)] == ["GET /a", "GET /b"]
    secret = tmp_path / "elsewhere.jsonl"
    secret.write_text(json.dumps({"line": "GET /planted"}) + "\n")
    log.unlink()
    log.symlink_to(secret)
    assert re_.read_log(log) is None
    assert re_.read_log(tmp_path / "missing.jsonl") is None
    assert re_.read_log(tmp_path) is None  # a folder is not a log


def run_with_fakes(monkeypatch, tmp_path, case, agent_work):
    """run_case with the agent replaced by `agent_work(env)`, which acts as the agent would."""
    real_run = subprocess.run

    def fake_agent(cmd, **kw):
        agent_work(kw["env"], real_run)
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(re_.subprocess, "run", fake_agent)
    out = tmp_path / "out"
    out.mkdir()
    return re_.run_case("claude", case, out, None, 2, sandbox=a_sandbox(tmp_path)), out


def post_issue(env, summary):
    body = {
        "fields": {
            "project": {"key": "AS"},
            "issuetype": {"name": "Improvement"},
            "summary": summary,
            "labels": ["skill-feedback"],
            "components": [{"name": "weather-skills"}],
        }
    }
    req = urllib.request.Request(
        env["ECMWF_WEATHER_JIRA_URL"] + "/rest/api/2/issue",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {env['JIRA_PAT']}", "Content-Type": "application/json"},
        method="POST",
    )
    return urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=10)


def test_run_case_grades_what_the_fakes_logged_and_records_the_logs(monkeypatch, tmp_path):
    case = {
        **FAKE_CASE,
        "expect": [
            {"kind": "jira_count", "pattern": "^CREATED .*issuetype=Improvement&", "count": 1},
            {"kind": "jira_hit", "pattern": "&components=weather-skills&"},
            {"kind": "gh_count", "pattern": "^gh issue create ", "count": 1},
            {"kind": "gh_hit", "pattern": "--label skill-feedback"},
            {"kind": "gh_no_write"},
        ],
    }
    seen = {}

    def agent(env, run):
        seen["url"] = env["ECMWF_WEATHER_JIRA_URL"]
        assert post_issue(env, "[ecmwf-weather] One").status == 201
        run(
            [
                *("gh", "issue", "create", "-R", re_.GH_REPO, "--title", "T", "--body", "b"),
                *("--label", "skill-feedback"),
            ],
            env=env,
            capture_output=True,
            check=True,
        )

    res, out = run_with_fakes(monkeypatch, tmp_path, case, agent)
    results = {r["kind"]: r for r in res["results"]}
    # gh_no_write fails: the create is a write; everything else holds
    assert [r["ok"] for r in res["results"]] == [True, True, True, True, False]
    assert not res["passed"] and res["error"] is None
    assert res["jira_log"] == str(out / "fb-fake.claude.2.jira.jsonl")
    assert res["gh_log"] == str(out / "fb-fake.claude.2.gh.jsonl")
    assert Path(res["jira_log"]).is_file() and Path(res["gh_log"]).is_file()
    assert results["gh_no_write"]["detail"]
    # torn down: the fake JIRA no longer answers
    with pytest.raises(OSError):
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            seen["url"] + "/rest/api/2/serverInfo", timeout=2
        )


def test_run_case_counts_a_second_create_as_a_failure(monkeypatch, tmp_path):
    case = {
        **FAKE_CASE,
        "expect": [{"kind": "jira_count", "pattern": "^CREATED ", "count": 1}],
    }

    def agent(env, run):
        post_issue(env, "[ecmwf-weather] One")
        post_issue(env, "[ecmwf-weather] Two")

    res, _ = run_with_fakes(monkeypatch, tmp_path, case, agent)
    assert not res["results"][0]["ok"] and "2 matching" in res["results"][0]["detail"]


def test_run_case_tears_the_fakes_down_when_the_agent_run_raises(monkeypatch, tmp_path):
    seen = {}

    def agent(env, run):
        seen["url"] = env["ECMWF_WEATHER_JIRA_URL"]
        raise RuntimeError("the agent exploded")

    with pytest.raises(RuntimeError, match="exploded"):
        run_with_fakes(monkeypatch, tmp_path, FAKE_CASE, agent)
    with pytest.raises(OSError):
        urllib.request.build_opener(urllib.request.ProxyHandler({})).open(
            seen["url"] + "/rest/api/2/serverInfo", timeout=2
        )


def test_run_case_reports_a_fake_that_did_not_start_as_agent_did_not_run(monkeypatch, tmp_path):
    def never(cmd, **kw):
        raise AssertionError("the agent must not run without its fakes")

    monkeypatch.setattr(re_.subprocess, "run", never)
    monkeypatch.setattr(re_, "FAKE_JIRA", tmp_path / "missing.py")
    out = tmp_path / "out"
    out.mkdir()
    case = {**FAKE_CASE, "expect": [{"kind": "jira_hit", "pattern": "x"}]}
    res = re_.run_case("claude", case, out, None, 1, sandbox=a_sandbox(tmp_path))
    assert res["error"].startswith("fake services did not start")
    assert "fake JIRA did not start" in res["error"] and not res["passed"]
    assert re_.summary_line([res]) == "0/1 passed, 0 failed, 1 error (agent did not run)"


def test_run_case_without_fakes_has_no_logs(monkeypatch, tmp_path):
    seen = {}

    def agent(env, run):
        seen["env"] = env

    case = {"id": "plain", "prompt": "p", "expect": []}
    res, _ = run_with_fakes(monkeypatch, tmp_path, case, agent)
    assert res["jira_log"] is None and res["gh_log"] is None
    assert seen["env"]["PWD"] == res["workdir"]


def test_the_gh_shim_names_the_fake_with_its_config_and_log(tmp_path):
    case = case_with([], {"gh": {}})
    with re_.FakeServices(case, tmp_path, "c") as fakes:
        shim = (fakes.gh_bin / "gh").read_text()
    assert shim.startswith("#!/bin/sh\nexec ")
    assert "fake_gh.py" in shim and "--config" in shim and str(fakes.gh_log) in shim
    assert sys.executable in shim
