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
    assert (
        oc[oc.index("--dir") + 1] == str(tmp_path)
        and oc[oc.index("-m") + 1] == "anthropic/claude-opus-4-8"
    )
    pi = re_.agent_command("pi", "hi", tmp_path, {}, plugin=tmp_path / "plug", model="x/y")
    assert pi[0] == "pi" and pi[pi.index("--mode") + 1] == "json" and "--no-session" in pi
    assert pi[pi.index("--skill") + 1] == str(tmp_path / "plug" / "skills")
    assert pi[pi.index("--model") + 1] == "x/y"


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
