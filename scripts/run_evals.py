#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Run skill evals: a fresh, context-free agent with only this plugin loaded.

  python3 scripts/run_evals.py                         # all cases, Claude Code
  python3 scripts/run_evals.py --case od-latest-run    # one case
  python3 scripts/run_evals.py --agent codex           # portability
  python3 scripts/run_evals.py --model sonnet --repeat 3
  python3 scripts/run_evals.py --no-mcp                # without the bundled ECMWF MCP server

Each case runs in a new empty temp directory. The ECMWF MCP server the plugin bundles is
configured for every agent the way that agent reads it (--no-mcp removes it). Transcripts +
summary.json go to evals/runs/<timestamp>/. See AGENTS.md -> Testing for the method.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins" / "ecmwf-weather"
SKILLS = PLUGIN / "skills"
KINDS = {
    "skill",
    "command",
    "command_not",
    "final",
    "final_not",
    "file",
    "image_size",
    "mcp",
    "mcp_not",
    "web_not",
    "any",
}
SKILL_PATH_RE = re.compile(r"skills/([a-z0-9-]+)/(?:SKILL\.md|scripts/|references/)")
# Calls to the ECMWF MCP server made through the skills' stdlib client count as MCP calls.
MCP_SCRIPT_CALL = re.compile(r"ecmwf_mcp\.py\s+call\s+([A-Za-z0-9_]+)")
# pi's default "codemode" exposure calls MCP tools from code as mcp__<server>__<tool>.
MCP_IN_CODE = re.compile(r"\bmcp__([A-Za-z0-9_-]+?)__([A-Za-z0-9_]+)")
WEB_TOOLS = {
    "claude": ("WebSearch", "WebFetch"),
    "opencode": ("websearch", "webfetch"),
    "gemini": ("google_web_search", "web_fetch"),
}


# --- transcripts --------------------------------------------------------------------------------


def _skills_from_text(text: str) -> set[str]:
    return set(SKILL_PATH_RE.findall(text or ""))


def _mcp_name(name: str) -> str:
    """mcp__<server>__<tool> (Claude, pi) -> "<server>:<tool>"."""
    parts = name.split("__")
    return f"{parts[1]}:{'__'.join(parts[2:])}" if len(parts) >= 3 else name


def parse_transcript(agent: str, raw: str, mcp_servers=("ecmwf",)) -> dict:
    # "code": text the agent wrote into files — graded with commands, since agents often put
    # their code in a file and only run `uv run x.py` in the shell.
    # "mcp": MCP tool calls as "<server>:<tool>"; "web": web searches and fetches;
    # "mcp_servers": each MCP server's connection state at start-up, where the agent reports it.
    t = {
        "skills": set(),
        "commands": [],
        "code": "",
        "final": "",
        "cost_usd": None,
        "error": None,
        "mcp": [],
        "web": [],
        "mcp_servers": {},
    }
    for line in raw.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if agent == "claude":
            if ev.get("type") == "system" and ev.get("subtype") == "init":
                for server in ev.get("mcp_servers") or []:
                    t["mcp_servers"][str(server.get("name"))] = server.get("status")
            elif ev.get("type") == "assistant":
                for c in ev.get("message", {}).get("content", []):
                    if c.get("type") != "tool_use":
                        continue
                    inp = c.get("input", {})
                    if str(c.get("name", "")).startswith("mcp__"):
                        t["mcp"].append(_mcp_name(c["name"]))
                    elif c.get("name") in WEB_TOOLS["claude"]:
                        t["web"].append(f"{c['name']}: {inp.get('query') or inp.get('url', '')}")
                    if c.get("name") == "Skill":
                        t["skills"].add(str(inp.get("skill", "")).split(":")[-1])
                    elif c.get("name") == "Bash":
                        t["commands"].append(inp.get("command", ""))
                        t["skills"] |= _skills_from_text(inp.get("command", ""))
                    elif c.get("name") in ("Write", "Edit", "MultiEdit"):
                        t["code"] += "\n" + str(
                            inp.get("content")
                            or inp.get("new_string")
                            or json.dumps(inp.get("edits", ""))
                        )
                        t["skills"] |= _skills_from_text(json.dumps(inp))
                    else:
                        t["skills"] |= _skills_from_text(json.dumps(inp))
            elif ev.get("type") == "result":
                t["final"] = ev.get("result", "") or ""
                t["cost_usd"] = ev.get("total_cost_usd")
                if ev.get("is_error") and ev.get("terminal_reason") == "api_error":
                    t["error"] = t["final"] or "api_error"
        elif agent == "codex":
            item = ev.get("item", {})
            if ev.get("type") == "item.completed" and item.get("type") == "command_execution":
                t["commands"].append(item.get("command", ""))
                t["skills"] |= _skills_from_text(item.get("command", ""))
            elif ev.get("type") == "item.completed" and item.get("type") == "mcp_tool_call":
                t["mcp"].append(f"{item.get('server')}:{item.get('tool')}")
            elif ev.get("type") == "item.completed" and item.get("type") == "web_search":
                action = item.get("action") or {}
                seen = [item.get("query"), action.get("url"), *(action.get("queries") or [])]
                t["web"].append("web_search: " + " | ".join(str(x) for x in seen if x))
            elif ev.get("type") == "item.completed" and item.get("type") == "agent_message":
                t["final"] = item.get("text", "")
            elif ev.get("type") in ("error", "turn.failed"):
                t["error"] = json.dumps(ev)[:300]
        elif agent == "opencode":
            part = ev.get("part", {})
            if ev.get("type") == "tool_use" and part.get("type") == "tool":
                inp = part.get("state", {}).get("input", {})
                tool = str(part.get("tool", ""))
                server = next((s for s in mcp_servers if tool.startswith(f"{s}_")), None)
                if server:  # opencode names MCP tools <server>_<tool>
                    t["mcp"].append(f"{server}:{tool[len(server) + 1 :]}")
                elif tool in WEB_TOOLS["opencode"]:
                    t["web"].append(f"{tool}: {inp.get('query') or inp.get('url', '')}")
                if part.get("tool") == "skill":
                    t["skills"].add(str(inp.get("name", "")))
                elif part.get("tool") in ("write", "edit", "apply_patch"):
                    t["code"] += "\n" + str(
                        inp.get("content") or inp.get("newString") or inp.get("patchText") or ""
                    )
                elif part.get("tool") == "bash":
                    # opencode passes the directory separately; keep it with the command
                    wd = inp.get("workdir")
                    cmd = inp.get("command", "")
                    t["commands"].append(f"cd {wd} && {cmd}" if wd else cmd)
                    t["skills"] |= _skills_from_text(json.dumps(inp))
                else:
                    t["skills"] |= _skills_from_text(json.dumps(inp))
            elif ev.get("type") == "text" and part.get("text"):
                t["final"] = part["text"]  # the last text part is the answer
            elif ev.get("type") == "error":
                t["error"] = json.dumps(ev.get("error", ev))[:300]
        elif agent == "pi":
            if ev.get("type") == "tool_execution_start":
                args = ev.get("args", {})
                name = str(ev.get("toolName", ""))
                if name.startswith("mcp__"):
                    t["mcp"].append(_mcp_name(name))
                elif name == "codemode":
                    code = json.dumps(args)
                    t["mcp"] += [f"{srv}:{tool}" for srv, tool in MCP_IN_CODE.findall(code)]
                if ev.get("toolName") == "bash":
                    t["commands"].append(args.get("command", ""))
                t["skills"] |= _skills_from_text(json.dumps(args))
            elif (
                ev.get("type") == "message_end" and ev.get("message", {}).get("role") == "assistant"
            ):
                m = ev["message"]
                if m.get("stopReason") == "error":
                    t["error"] = str(m.get("errorMessage", "error"))[:300]
                text = "".join(
                    c.get("text", "") for c in m.get("content", []) if c.get("type") == "text"
                )
                if text.strip():
                    t["final"] = text
    if agent == "gemini":  # one pretty-printed JSON document, possibly after banner lines
        i = raw.find("{")
        try:
            doc = json.loads(raw[i:]) if i >= 0 else {}
        except json.JSONDecodeError:
            doc = {}
        t["final"] = doc.get("response", "") or ""
        used = ((doc.get("stats") or {}).get("tools") or {}).get("byName") or {}
        t["web"] = [name for name in used if name in WEB_TOOLS["gemini"]]
        if doc.get("error"):
            t["error"] = str(doc["error"].get("message", doc["error"]))[:300]
        t["skills"] |= _skills_from_text(raw)
    return t


# --- grading ------------------------------------------------------------------------------------


def _commands_text(t: dict) -> str:
    # Agents quote paths ("$DIR/cds.py" check) and keep script paths in shell variables
    # (S=.../mars.py; python3 $S lint) — match on the expanded command without quotes.
    text = "\n".join([*t["commands"], t.get("code", "")])
    return expand_shell_vars(text).replace('"', "").replace("'", "")


def mcp_calls(t: dict) -> list[str]:
    """MCP tool calls: the agent's own MCP tools, then calls through the skills' script."""
    script = [f"ecmwf_mcp.py:{tool}" for tool in MCP_SCRIPT_CALL.findall(_commands_text(t))]
    return [*t.get("mcp", []), *script]


def grade(t: dict, expect: list[dict], workdir: Path | None = None) -> list[dict]:
    out = []
    cmds = _commands_text(t)
    calls = mcp_calls(t)
    for e in expect:
        k = e["kind"]
        if k == "skill":
            ok = e["name"] in t["skills"]
        elif k == "command":
            ok = re.search(e["pattern"], cmds) is not None
        elif k == "command_not":
            ok = re.search(e["pattern"], cmds) is None
        elif k == "final":
            ok = re.search(e["pattern"], t["final"]) is not None
        elif k == "final_not":
            ok = re.search(e["pattern"], t["final"]) is None
        elif k == "file":
            ok = workdir is not None and any(workdir.glob(e["pattern"]))
        elif k == "image_size":
            ok = workdir is not None and any(
                _png_size(f) == tuple(e["size"]) for f in workdir.glob(e["pattern"])
            )
        elif k == "mcp":
            ok = any(re.search(e["pattern"], c) for c in calls)
        elif k == "mcp_not":
            ok = not any(re.search(e["pattern"], c) for c in calls)
        elif k == "web_not":  # no web search or fetch at all, or none matching the pattern
            web = t.get("web", [])
            ok = not any(re.search(e["pattern"], w) for w in web) if e.get("pattern") else not web
        elif k == "any":  # passes when one of several acceptable routes was taken
            ok = any(r["ok"] for r in grade(t, e["of"], workdir))
        else:
            raise ValueError(f"unknown expectation kind {k}")
        out.append({**e, "ok": ok})
    return out


_ASSIGN = re.compile(r"(?:^|[\s;&|])([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|'[^']*'|[^\s;&|]+)")


def expand_shell_vars(text: str) -> str:
    """Substitute $VAR / ${VAR} with values assigned earlier in the same commands."""
    values = {name: val.strip("\"'") for name, val in _ASSIGN.findall(text)}
    for name in sorted(values, key=len, reverse=True):
        text = re.sub(r"\$\{" + name + r"\}|\$" + name + r"\b", lambda _m, n=name: values[n], text)
    return text


def _png_size(path: Path) -> tuple[int, int] | None:
    """Width and height from a PNG header (no image library needed)."""
    import struct

    head = path.read_bytes()[:24]
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    return struct.unpack(">II", head[16:24])


def case_env(case: dict, base: dict | None = None) -> dict:
    """Environment for one case: the caller's, plus the case's `setup.env` overrides (e.g.
    point CDSAPI_RC at a missing file to simulate a user without a CDS key)."""
    return {**(os.environ if base is None else base), **case.get("setup", {}).get("env", {})}


def new_run_dir(base: Path, stamp: str | None = None) -> Path:
    """A fresh directory per run; parallel runs started in the same second get a suffix."""
    stamp = stamp or time.strftime("%Y%m%d-%H%M%S")
    base.mkdir(parents=True, exist_ok=True)
    for n in range(1000):
        d = base / (stamp if n == 0 else f"{stamp}-{n}")
        try:
            d.mkdir()
            return d
        except FileExistsError:
            continue
    raise RuntimeError(f"cannot create a run directory under {base}")


def note_startup_failure(t: dict, rc: int, stderr: str) -> dict:
    """A non-zero exit with no answer and no commands means the agent never ran (missing
    login or API key, bad model name) — report it as an error, not a skill failure."""
    if t["error"] is None and rc != 0 and not t["final"] and not t["commands"]:
        lines = [ln for ln in (stderr or "").strip().splitlines() if ln.strip()]
        t["error"] = (lines[0] if lines else f"agent exited with code {rc}")[:300]
    return t


CODEX_SESSIONS = Path.home() / ".codex" / "sessions"


def codex_written_code(workdir: Path, sessions: Path = CODEX_SESSIONS) -> str:
    """File edits Codex made in `workdir`. Its --json stream reports only that a file changed;
    the patch text is in Codex's own session log, matched by working directory."""
    out = []
    for log in sorted(Path(sessions).rglob("rollout-*.jsonl"), reverse=True)[:400]:
        lines = log.read_text(errors="ignore").splitlines()
        if not lines or str(workdir) not in lines[0]:
            continue
        for line in lines:
            try:
                p = json.loads(line).get("payload", {})
            except json.JSONDecodeError:
                continue
            body = p.get("input") or p.get("arguments") or ""
            if isinstance(body, str) and "*** Begin Patch" in body:
                out.append(body.encode().decode("unicode_escape", errors="ignore"))
        break
    return "\n".join(out)


def cleanup_case(plugin: Path, workdir: Path) -> None:
    """After grading: remove the case's skill copies so later agents searching the filesystem
    can never find stale skills; keep the workdir's outputs for inspection."""
    shutil.rmtree(
        plugin.parent if plugin.parent.name.startswith("ecmwf-eval-plugin-") else plugin,
        ignore_errors=True,
    )
    for d in (".agents", ".gemini"):
        shutil.rmtree(workdir / d, ignore_errors=True)


def summary_line(results: list[dict]) -> str:
    passed = sum(r["passed"] for r in results)
    errors = sum(1 for r in results if r.get("error"))
    failed = len(results) - passed - errors
    return f"{passed}/{len(results)} passed, {failed} failed, {errors} error (agent did not run)"


def load_cases(path: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(Path(path).glob("*.json"))]


# --- running ------------------------------------------------------------------------------------


def mcp_servers(plugin: Path = PLUGIN) -> dict[str, str]:
    """Server name -> URL of the MCP servers the plugin bundles (its portable mcp.json)."""
    doc = json.loads((Path(plugin) / "mcp.json").read_text())
    return {name: cfg["url"] for name, cfg in doc.get("mcpServers", {}).items()}


def plugin_copy(base: Path, mcp: bool = True) -> Path:
    """A private copy of the plugin for one case: agents sometimes write files next to the
    scripts they run, and must never be able to modify the skills under test. Without `mcp`
    the copy has no bundled MCP server, as for a user who turned it off."""
    dst = Path(base) / PLUGIN.name
    shutil.copytree(PLUGIN, dst, ignore=shutil.ignore_patterns("__pycache__", ".cache", "*.pyc"))
    if not mcp:
        for name in (".mcp.json", "mcp.json"):
            (dst / name).unlink(missing_ok=True)
    return dst


def agent_command(
    agent: str,
    prompt: str,
    workdir: Path,
    case: dict,
    model: str | None = None,
    plugin: Path = PLUGIN,
    mcp: bool = True,
) -> list[str]:
    # The bundled MCP server reaches each agent the way it reads servers: Claude Code from the
    # plugin copy's .mcp.json, Codex from -c overrides, opencode/gemini/pi from files that
    # prepare_workdir writes.
    if agent == "claude":
        cmd = [
            "claude",
            "-p",
            prompt,
            "--plugin-dir",
            str(plugin),
            "--setting-sources",
            "project",
            "--output-format",
            "stream-json",
            "--verbose",
            "--permission-mode",
            "bypassPermissions",
            "--max-budget-usd",
            str(case.get("max_budget_usd", 2)),
        ]
        if os.environ.get("EVAL_BARE") == "1":  # CI: strict isolation, needs ANTHROPIC_API_KEY
            cmd.append("--bare")
            # --bare skips the plugin's MCP servers: pass its .mcp.json (tools mcp__ecmwf__*).
            if mcp and (plugin / ".mcp.json").exists():
                cmd += ["--mcp-config", str(plugin / ".mcp.json")]
    elif agent == "codex":
        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "-C",
            str(workdir),
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
        ]
        if mcp:
            for name, url in mcp_servers(plugin).items():
                cmd += ["-c", f'mcp_servers.{name}.url="{url}"']
        cmd.append(prompt)
    elif agent == "gemini":
        cmd = ["gemini", "-p", prompt, "-o", "json", "--approval-mode", "yolo"]
    elif agent == "opencode":
        # Skills come from <workdir>/.agents/skills (prepare_workdir); --auto approves tools.
        cmd = ["opencode", "run", "--format", "json", "--auto", "--dir", str(workdir)]
        cmd += ["-m", model] if model else []
        return [*cmd, prompt]
    elif agent == "pi":
        # pi runs tools without approval; skills are passed explicitly, no session is saved.
        cmd = ["pi", "--mode", "json", "--no-session", "--skill", str(plugin / "skills")]
        # pi reads <workdir>/.pi/mcp.json only in a trusted project; --approve trusts it for
        # this run. --no-mcp also keeps the user's own servers out.
        cmd += ["--approve"] if mcp else ["--no-mcp"]
        cmd += ["--model", model] if model else []
        return [*cmd, prompt]
    else:
        raise ValueError(agent)
    if model:
        cmd[1:1] = ["--model", model] if agent != "codex" else []
        if agent == "codex":
            cmd[-1:-1] = ["-m", model]
    return cmd


def _write_json(path: Path, doc: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")


def prepare_workdir(agent: str, case: dict, plugin: Path = PLUGIN, mcp: bool = True) -> Path:
    wd = Path(tempfile.mkdtemp(prefix=f"ecmwf-eval-{case['id']}-"))
    for src, dst in case.get("setup", {}).get("copy", {}).items():
        shutil.copy(ROOT / src, wd / dst)
    servers = mcp_servers(plugin) if mcp else {}
    if servers and agent == "opencode":  # project config, as `opencode mcp add` would write
        _write_json(
            wd / "opencode.json",
            {
                "$schema": "https://opencode.ai/config.json",
                "mcp": {
                    n: {"type": "remote", "url": u, "enabled": True} for n, u in servers.items()
                },
            },
        )
    elif servers and agent == "gemini":  # as `gemini mcp add -s project -t http` writes it
        _write_json(
            wd / ".gemini" / "settings.json",
            {"mcpServers": {n: {"url": u, "type": "http"} for n, u in servers.items()}},
        )
    elif servers and agent == "pi":  # as `pi mcp add -l --url` writes it
        _write_json(
            wd / ".pi" / "mcp.json", {"mcpServers": {n: {"url": u} for n, u in servers.items()}}
        )
    if agent in ("codex", "gemini", "opencode"):
        d = wd / (".gemini/skills" if agent == "gemini" else ".agents/skills")
        d.mkdir(parents=True, exist_ok=True)
        for s in (plugin / "skills").iterdir():
            if (s / "SKILL.md").exists():
                # copies, not symlinks: `find -type f` does not descend into symlinked dirs
                shutil.copytree(s, d / s.name)
    return wd


def run_case(
    agent: str, case: dict, outdir: Path, model: str | None, attempt: int, mcp: bool = True
) -> dict:
    plugin = plugin_copy(Path(tempfile.mkdtemp(prefix="ecmwf-eval-plugin-")), mcp=mcp)
    servers = tuple(mcp_servers(PLUGIN)) if mcp else ()
    wd = prepare_workdir(agent, case, plugin, mcp=mcp)
    cmd = agent_command(agent, case["prompt"], wd, case, model, plugin=plugin, mcp=mcp)
    t0 = time.time()
    try:
        p = subprocess.run(
            cmd,
            cwd=wd,
            capture_output=True,
            text=True,
            timeout=case.get("timeout", 900),
            env=case_env(case),
        )
        raw, err, rc = p.stdout, p.stderr, p.returncode
    except subprocess.TimeoutExpired as e:
        raw, err, rc = (
            (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
            "TIMEOUT",
            -1,
        )
    name = f"{case['id']}.{agent}.{attempt}"
    (outdir / f"{name}.jsonl").write_text(raw)
    if err:
        (outdir / f"{name}.stderr").write_text(err)
    t = note_startup_failure(parse_transcript(agent, raw, mcp_servers=servers), rc, err)
    if agent == "codex":
        t["code"] += codex_written_code(wd)
    results = grade(t, case["expect"], wd)
    cleanup_case(plugin, wd)
    return {
        "id": case["id"],
        "agent": agent,
        "attempt": attempt,
        "rc": rc,
        "seconds": round(time.time() - t0),
        "cost_usd": t["cost_usd"],
        "skills": sorted(t["skills"]),
        "mcp_server": bool(servers),
        "mcp_server_status": t["mcp_servers"],
        "mcp_calls": mcp_calls(t),
        "web": t["web"],
        "error": t["error"],
        "passed": t["error"] is None and all(r["ok"] for r in results),
        "results": results,
        "workdir": str(wd),
        "final": t["final"][-1500:],
    }


def parse_args(argv=None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument(
        "--agent", default="claude", choices=["claude", "codex", "gemini", "opencode", "pi"]
    )
    ap.add_argument("--case", action="append", help="case id (repeatable)")
    ap.add_argument("--model")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument(
        "--no-mcp",
        dest="mcp",
        action="store_false",
        help="run without the ECMWF MCP server the plugin bundles",
    )
    return ap.parse_args(argv)


def _failed(x: dict) -> str:
    if x["kind"] == "any":
        return "any:[" + " | ".join(_failed(e) for e in x["of"]) + "]"
    return f"{x['kind']}:{x.get('name') or x.get('pattern') or ''}"


def main(argv=None) -> int:
    a = parse_args(argv)

    cases = load_cases(ROOT / "evals" / "cases")
    if a.case:
        cases = [c for c in cases if c["id"] in a.case]
    outdir = new_run_dir(ROOT / "evals" / "runs")
    servers = ", ".join(f"{n} {u}" for n, u in mcp_servers().items())
    print(f"MCP server: {servers if a.mcp else 'off (--no-mcp)'}", flush=True)
    summary = []
    for c in cases:
        for i in range(a.repeat):
            r = run_case(a.agent, c, outdir, a.model, i + 1, mcp=a.mcp)
            summary.append(r)
            mark = "PASS" if r["passed"] else ("ERROR" if r["error"] else "FAIL")
            if r["error"]:
                print(
                    f"ERROR {c['id']} [{a.agent}#{i + 1}] agent did not run: {r['error']}",
                    flush=True,
                )
                continue
            fails = [_failed(x) for x in r["results"] if not x["ok"]]
            print(
                f"{mark} {c['id']} [{a.agent}#{i + 1}] {r['seconds']}s ${r['cost_usd']} "
                f"skills={r['skills']} mcp={r['mcp_calls']} web={len(r['web'])} "
                f"{'failed=' + str(fails) if fails else ''}",
                flush=True,
            )
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2))
    n = sum(r["passed"] for r in summary)
    print(f"\n{summary_line(summary)} — {outdir}")
    return 0 if n == len(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
