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

A case can ask for fakes with `setup.fakes`, started for that case alone and stopped after it:

  "fakes": {"jira": {"issues": [...]}, "gh": {"login": "eval-colleague", "signed_in": true}}

`jira` starts tests/fakes/fake_jira.py (the URL reaches the agent as ECMWF_WEATHER_JIRA_URL and
the fake's synthetic token as JIRA_PAT); `gh` puts tests/fakes/fake_gh.py first on PATH as `gh`.
Their logs, beside the transcript, are graded with the jira_* and gh_* expectation kinds; see
those files for the log line formats.
"""

from __future__ import annotations

import argparse
import functools
import importlib.util
import json
import os
import queue
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
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
    "jira_hit",
    "jira_not_hit",
    "jira_count",
    "gh_hit",
    "gh_not_hit",
    "gh_count",
    "gh_no_write",
}
# Expectations over what a fake logged (see tests/fakes). hit / not_hit: a regex over the logged
# lines; count: exactly `count` lines match `pattern` ("exactly one issue created");
# gh_no_write: no logged call is a write.
JIRA_KINDS = {"jira_hit", "jira_not_hit", "jira_count"}
GH_KINDS = {"gh_hit", "gh_not_hit", "gh_count", "gh_no_write"}
FAKES_DIR = ROOT / "tests" / "fakes"
FAKE_JIRA = FAKES_DIR / "fake_jira.py"
FAKE_GH = FAKES_DIR / "fake_gh.py"
# The repository the fake gh knows (REPO in tests/fakes/fake_gh.py; a test keeps them equal).
GH_REPO = "ecmwf/ecmwf-weather-skills"
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
    # Paths are often built from shell variables (S=…/skills/x/scripts; uv run $S/y.py).
    return set(SKILL_PATH_RE.findall(expand_shell_vars(text or "")))


# Web access to ECMWF's and Copernicus's own sites is not a fallback to third-party data.
ECMWF_WEB_DOMAIN = re.compile(
    r"(?i)^(?:[a-z0-9-]+\.)*(?:ecmwf\.int|copernicus\.eu|destination-earth\.eu|destine\.eu)$"
)
DOMAIN = re.compile(r"(?i)\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b")


def _web_is_ecmwf_only(entry: str) -> bool:
    domains = DOMAIN.findall(entry.split(":", 1)[-1])
    return bool(domains) and all(ECMWF_WEB_DOMAIN.match(d) for d in domains)


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
                if tool == "skill":  # the skill is `id` (opencode 2), `name` (earlier versions)
                    if name := str(inp.get("id") or inp.get("name") or ""):
                        t["skills"].add(name)
                elif tool in ("write", "edit", "apply_patch"):
                    t["code"] += "\n" + str(
                        inp.get("content") or inp.get("newString") or inp.get("patchText") or ""
                    )
                elif tool in ("shell", "bash"):  # `shell` in opencode 2, `bash` before
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


def _log_lines(log: list[dict]) -> list[str]:
    return [x["line"] for x in log if isinstance(x.get("line"), str)]


def _gh_call_name(line: str) -> str:
    """The command of a logged gh call as "gh <group> <subcommand>": no argument or value."""
    try:
        words = shlex.split(line)
    except ValueError:
        words = line.split()
    named = [w for w in words[1:] if not w.startswith("-")][:2]
    return " ".join(["gh", *named])


def _grade_fake_log(e: dict, jira_log: list[dict] | None, gh_log: list[dict] | None) -> dict:
    """Grade a jira_* or gh_* expectation against the fake's log (a list of entries with a
    `line`; the gh entries also a `kind`). A fake with no log fails with the reason."""
    k = e["kind"]
    fake, log = ("JIRA", jira_log) if k in JIRA_KINDS else ("gh", gh_log)
    if log is None:
        why = f"the fake {fake} log is not available (no fake configured, or its log was not read)"
        return {**e, "ok": False, "detail": why}
    if k == "gh_no_write":
        writes = [x for x in log if x.get("kind") == "write"]
        if not writes:
            return {**e, "ok": True}
        name = _gh_call_name(str(writes[0].get("line", "")))
        return {**e, "ok": False, "detail": f"a gh write was attempted: {name}"}
    lines = _log_lines(log)
    matching = sum(1 for ln in lines if re.search(e["pattern"], ln))
    if k.endswith("_count"):
        if matching == e["count"]:
            return {**e, "ok": True}
        return {**e, "ok": False, "detail": f"{matching} matching line(s), expected {e['count']}"}
    hit = matching > 0
    return {**e, "ok": not hit if k.endswith("_not_hit") else hit}


def grade(
    t: dict,
    expect: list[dict],
    workdir: Path | None = None,
    jira_log: list[dict] | None = None,
    gh_log: list[dict] | None = None,
) -> list[dict]:
    """One result per expectation: the expectation plus `ok` (and a `detail` for a failed
    fake-log expectation). `jira_log` / `gh_log` are the entries of the fake's log (see
    read_log); a jira_* or gh_* expectation without its log fails."""
    out = []
    cmds = _commands_text(t)
    calls = mcp_calls(t)
    for e in expect:
        k = e["kind"]
        if k in JIRA_KINDS or k in GH_KINDS:
            out.append(_grade_fake_log(e, jira_log, gh_log))
            continue
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
            web = [w for w in t.get("web", []) if not _web_is_ecmwf_only(w)]
            ok = not any(re.search(e["pattern"], w) for w in web) if e.get("pattern") else not web
        elif k == "any":  # passes when one of several acceptable routes was taken
            ok = any(r["ok"] for r in grade(t, e["of"], workdir, jira_log, gh_log))
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


# --- isolation ----------------------------------------------------------------------------------
#
# GitHub and JIRA issues can only be filed with a credential, so no agent a case starts may see
# one. Every agent runs inside a bubblewrap sandbox that hides the places credentials live and
# maps the issue-service hosts to an address nothing listens on; its environment is an allow
# list with the issue-service variables removed; and the runner proves all of it with a probe
# run inside the same sandbox before the first case (and refuses to run without it).

# No host of the real issue services answers: this loopback address has no listener. 127.0.0.9
# rather than 127.0.0.1, where the fakes (and any local server) listen.
UNUSED_ADDRESS = "127.0.0.9"
BLOCKED_HOSTS = ("github.com", "api.github.com", "uploads.github.com", "jira.ecmwf.int")
# Where a gh login, a JIRA token or a git credential lives; relative to the home folder.
HIDDEN_HOME_DIRS = (".config/gh", ".ssh", ".local/share/keyrings")
HIDDEN_HOME_FILES = (".env", ".git-credentials", ".netrc", ".config/git/credentials")
# The session bus and keyring sockets (gh falls back to the keyring), under $XDG_RUNTIME_DIR.
HIDDEN_RUNTIME_ENTRIES = ("bus", "keyring", "gcr")

# What an agent gets from the caller's environment; everything else stays behind, so a variable
# added to the caller's shell tomorrow does not leak. Names are compared upper-cased.
ALLOWED_ENV = frozenset(
    {
        "PATH",
        "HOME",
        "USER",
        "LOGNAME",
        "SHELL",
        "TERM",
        "COLORTERM",
        "LANG",
        "LANGUAGE",
        "TZ",
        "TMPDIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "FTP_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
        "NODE_EXTRA_CA_CERTS",
    }
)
ALLOWED_ENV_PREFIXES = (
    "LC_",  # locale
    "XDG_",  # where agents keep their data and login (XDG_DATA_HOME, XDG_CONFIG_HOME)
    "ANTHROPIC_",
    "CLAUDE_",  # Claude Code's own login (CLAUDE_CODE_OAUTH_TOKEN) and settings
    "OPENAI_",
    "CODEX_",
    "OPENCODE_",
    "GEMINI_",
    "GOOGLE_",
    "PI_",
    "ECMWF_",  # ECMWF_SKILLS_CACHE, ECMWF_API_KEY, ...
    "CDSAPI_",
    "ADSAPI_",
    "DESTINE_",
    "POLYTOPE_",
    "MARS_",
    "EARTHKIT_",
    "MIR_",
    "ECCODES_",
    "UV_",
)
# Never reach an agent, however they arrived (caller's shell or a case's `setup.env`): any name
# naming JIRA, GitHub or gh (JIRA_PAT, ECMWF_JIRA_PAT, GH_TOKEN, GITHUB_TOKEN, GH_ENTERPRISE_TOKEN,
# GH_HOST, GH_REPO, ...), and the channels to a keyring or an agent holding keys.
FORBIDDEN_ENV = frozenset({"SSH_AUTH_SOCK", "DBUS_SESSION_BUS_ADDRESS"})
FORBIDDEN_ENV_PREFIXES = ("GH_", "GITHUB_")
FORBIDDEN_ENV_PARTS = ("JIRA",)
# Variables the runner sets itself, after the filtering; the probe does not count these.
RUNNER_SET_ENV = frozenset(
    {"GH_CONFIG_DIR", "GH_PROMPT_DISABLED", "GH_NO_UPDATE_NOTIFIER", "ECMWF_WEATHER_JIRA_URL"}
)
# The probe waits at most this long for the sandbox and for each gh call inside it: both end in
# well under a second; the limit only keeps a stuck process from hanging the runner.
PROBE_TIMEOUT_S = 60
PROBE_GH_TIMEOUT_S = 30
PROBE_CONNECT_TIMEOUT_S = 5
# What the fake gh prints when it has no configuration (tests/fakes/fake_gh.py): the shim a case
# without a gh fake gets.
ABSENT_GH_MESSAGE = "gh: command not found"


class SandboxError(RuntimeError):
    """The sandbox cannot be set up: nothing may run without it."""


def env_name_forbidden(name: str) -> bool:
    """Whether an environment variable of this name may never reach an agent or a test."""
    upper = name.upper()
    return (
        upper in FORBIDDEN_ENV
        or upper.startswith(FORBIDDEN_ENV_PREFIXES)
        or any(part in upper for part in FORBIDDEN_ENV_PARTS)
    )


def inherited_env(environ: Mapping[str, str]) -> dict:
    """The part of `environ` an agent may inherit: the allow list, nothing else."""
    return {
        k: v
        for k, v in environ.items()
        if k.upper() in ALLOWED_ENV or k.upper().startswith(ALLOWED_ENV_PREFIXES)
    }


def drop_forbidden_env(env: Mapping[str, str]) -> dict:
    return {k: v for k, v in env.items() if not env_name_forbidden(k)}


def hosts_with_no_proxy(value: str | None) -> str:
    """A NO_PROXY value that also lists the issue hosts, so a client goes to the (blocked)
    address in the sandbox's hosts file instead of asking a proxy to reach the real service."""
    entries = [e for e in (value or "").split(",") if e.strip()]
    return ",".join([*entries, *(h for h in BLOCKED_HOSTS if h not in entries)])


def closed_loopback_url() -> str:
    """http://127.0.0.1:<port> for a port nothing listens on, where a request is refused at once."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return f"http://127.0.0.1:{s.getsockname()[1]}"


@dataclass(frozen=True)
class Scratch:
    """What one case's environment points at: a `folder` holding a `gh` that looks absent (in
    `gh_bin`), an empty gh configuration folder, and the URL of a JIRA that is not there."""

    folder: Path
    gh_bin: Path
    gh_config: Path
    jira_url: str


def make_scratch(folder: Path, gh_log: Path) -> Scratch:
    """The scratch of one case in `folder`; calls of the absent `gh` are logged to `gh_log`,
    which must lie outside `folder`: the folder is read-only inside the sandbox."""
    folder = Path(folder)
    config = folder / "gh-config"
    config.mkdir(parents=True, exist_ok=True)
    gh_bin = folder / "bin"
    write_gh_shim(gh_bin, Path(gh_log))  # no config: the fake gh looks like no gh at all
    return Scratch(folder, gh_bin, config, closed_loopback_url())


def case_gh_shim(scratch: Scratch, fakes=None) -> Path:
    """The `gh` an agent of this case must reach: the case's fake, else the absent one."""
    gh_bin = fakes.gh_bin if fakes is not None and fakes.gh_bin is not None else scratch.gh_bin
    return gh_bin / "gh"


def case_sealed(scratch: Scratch, fakes=None) -> list[Path]:
    """The folders that hold what the case's `gh` relies on: the scratch folder (shim, fake
    script, gh configuration) and the fakes' private folder (shim, fake script, configuration,
    seed). They are mounted read-only over themselves inside the sandbox."""
    folders = [scratch.folder]
    if fakes is not None and fakes.folder is not None:
        folders.append(fakes.folder)
    return folders


def case_env(
    case: dict, scratch: Scratch, base: Mapping[str, str] | None = None, fakes=None
) -> dict:
    """Environment for one case, in this order: the caller's, filtered by the allow list; the
    case's `setup.env` overrides (e.g. point CDSAPI_RC at a missing file to simulate a user
    without a CDS key); the forbidden variables removed again, so a case cannot bring one back;
    then, last, what no case can undo — an empty gh configuration and no prompts, a `gh` first
    on PATH (the case's fake, else one that looks absent), and ECMWF_WEATHER_JIRA_URL on the
    case's fake JIRA (else on a closed port). Only a fake JIRA brings JIRA_PAT, its synthetic
    token."""
    env = {
        **inherited_env(os.environ if base is None else base),
        **case.get("setup", {}).get("env", {}),
    }
    env = drop_forbidden_env(env)
    env["PATH"] = os.pathsep.join(
        [str(case_gh_shim(scratch, fakes).parent), *filter(None, [env.get("PATH")])]
    )
    env.update(
        GH_CONFIG_DIR=str(scratch.gh_config), GH_PROMPT_DISABLED="1", GH_NO_UPDATE_NOTIFIER="1"
    )
    for var in ("NO_PROXY", "no_proxy"):
        env[var] = hosts_with_no_proxy(env.get(var))
    env["ECMWF_WEATHER_JIRA_URL"] = scratch.jira_url
    if fakes is not None and fakes.jira_url is not None:
        env["ECMWF_WEATHER_JIRA_URL"] = fakes.jira_url
        env["JIRA_PAT"] = fake_jira_token()
    return env


def write_hosts_file(path: Path, system_hosts: str | None = None) -> None:
    """A hosts file that keeps the system's entries and maps the issue hosts to UNUSED_ADDRESS.
    The mapping comes first: the resolver answers from the first matching line, so a system
    entry for one of these hosts must not win."""
    if system_hosts is None:
        try:
            system_hosts = Path("/etc/hosts").read_text(errors="replace")
        except OSError:
            system_hosts = ""
    block = f"# eval sandbox: the issue services are unreachable\n{UNUSED_ADDRESS} "
    Path(path).write_text(block + " ".join(BLOCKED_HOSTS) + "\n" + system_hosts)


def hidden_candidates(home: Path, environ: Mapping[str, str]) -> list[Path]:
    """Every location the sandbox hides, whether or not it exists: the credential places under
    `home`, the caller's own gh configuration folders, and the bus and keyring sockets. Parents
    come before their children, each path once."""
    paths = [Path(home) / p for p in (*HIDDEN_HOME_DIRS, *HIDDEN_HOME_FILES)]
    if environ.get("GH_CONFIG_DIR"):
        paths.append(Path(environ["GH_CONFIG_DIR"]))
    if environ.get("XDG_CONFIG_HOME"):
        paths.append(Path(environ["XDG_CONFIG_HOME"]) / "gh")
    if environ.get("XDG_RUNTIME_DIR"):
        paths += [Path(environ["XDG_RUNTIME_DIR"]) / e for e in HIDDEN_RUNTIME_ENTRIES]
    unique = {p: None for p in paths if p.is_absolute()}
    return sorted(unique, key=lambda p: len(p.parts))


# Where a package manager or an installer puts gh. A login shell may rewrite PATH so that none of
# the per-case folders on it is found first; whatever gh the agent then reaches must still be the
# case's shim, so the shim is also mounted over every real gh binary.
GH_BINARY_CANDIDATES = ("/usr/bin/gh", "/usr/local/bin/gh", "/opt/homebrew/bin/gh")
LOGIN_SHELL = "bash"


def gh_on_path(path: str | None) -> list[Path]:
    """Every executable file named gh in the folders of a PATH value, in order."""
    found = []
    for folder in (path or "").split(os.pathsep):
        if not folder:  # an empty entry means the current folder: not a fixed place
            continue
        gh = Path(folder) / "gh"
        if gh.is_file() and os.access(gh, os.X_OK):
            found.append(gh)
    return found


def login_shell_gh(environ: Mapping[str, str], run=subprocess.run) -> Path | None:
    """The gh the user's login shell finds, or None. Agents run commands as `bash -lc`, and a
    login shell reads the user's profile, which may put another gh first on PATH. Run once, by
    the runner, outside the sandbox; a shell that is absent, slow or answers with anything but
    the path of a file (an alias, a function) gives None."""
    shell = shutil.which(LOGIN_SHELL, path=environ.get("PATH")) or shutil.which(LOGIN_SHELL)
    if shell is None:
        return None
    try:
        p = run(
            [shell, "-lc", "command -v gh"],
            env=inherited_env(environ),
            capture_output=True,
            text=True,
            stdin=subprocess.DEVNULL,
            timeout=PROBE_GH_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    lines = (p.stdout or "").strip().splitlines()
    found = Path(lines[-1].strip()) if lines else None
    return found if found is not None and found.is_absolute() and found.is_file() else None


def real_gh_paths(
    environ: Mapping[str, str], run=subprocess.run, candidates: tuple[str, ...] | None = None
) -> list[Path]:
    """The real gh binaries an agent could reach: the known install locations that exist, every
    gh on the caller's PATH, and the one the login shell finds. Each once, in that order."""
    known = [Path(c) for c in (GH_BINARY_CANDIDATES if candidates is None else candidates)]
    found = [p for p in known if p.is_file()]
    found += gh_on_path(environ.get("PATH"))
    login = login_shell_gh(environ, run)
    if login is not None:
        found.append(login)
    return list({p: None for p in found})


class Sandbox:
    """The bubblewrap command line every case's agent runs under: the file system as it is, with
    the credential locations hidden, the issue hosts mapped to nothing, a private process list,
    and the case's gh shim mounted over every real gh binary (`real_gh`), so that no way of
    calling gh reaches a real one. `environ` is the caller's environment, read for the folders to
    hide."""

    def __init__(
        self,
        bwrap: str,
        home: Path,
        hosts_file: Path,
        environ: Mapping[str, str] | None = None,
        real_gh: Iterable[Path] = (),
    ):
        self.bwrap = str(bwrap)
        self.home = Path(home)
        self.hosts_file = Path(hosts_file)
        self.environ = dict(os.environ if environ is None else environ)
        self.real_gh = tuple(Path(p) for p in real_gh)

    def gh_replaced(self, gh_shim: Path) -> list[Path]:
        """The files the shim is mounted over: each real gh that exists, followed to the file a
        link leads to (a mount on a link's own path would not cover the other routes to the file),
        once, and never the shim itself. A missing one is left out: bwrap would create its mount
        point in the real file system."""
        shim = Path(gh_shim).resolve()
        files = {p.resolve(): None for p in self.real_gh if p.is_file()}
        return [p for p in files if p != shim]

    def hidden(self) -> list[tuple[str, Path]]:
        """("dir" | "file", path) for each hidden location that exists. A missing one is left
        out: bwrap would create its mount point in the real file system."""
        found = []
        for p in hidden_candidates(self.home, self.environ):
            if p.is_dir():
                found.append(("dir", p))
            elif p.exists():  # a file, or a socket
                found.append(("file", p))
        return found

    def sealed_paths(self, gh_shim: Path | None, sealed: Iterable[Path] = ()) -> list[Path]:
        """What is mounted read-only over itself: the hosts file, the shim and the `sealed`
        folders, each once and only if it exists (bwrap fails on a missing source)."""
        wanted = [self.hosts_file, *([Path(gh_shim)] if gh_shim is not None else []), *sealed]
        return list({Path(p).resolve(): None for p in wanted if Path(p).exists()})

    def seal_args(self, gh_shim: Path | None, sealed: Iterable[Path] = ()) -> list[str]:
        """The file system bind is read-write, so a file bound read-only at one path (the hosts
        file at /etc/hosts, the shim over the real gh) can still be rewritten through its own
        path, and a folder on PATH can be renamed away and recreated. Binding each file and each
        case folder read-only over itself leaves no writable path to the same files, and a mount
        point can be neither renamed nor removed. A hard link would need the same file system
        mount, which the seal makes a different one."""
        args = []
        for p in self.sealed_paths(gh_shim, sealed):
            args += ["--ro-bind", str(p), str(p)]
        return args

    def args(
        self, workdir: Path, gh_shim: Path | None = None, sealed: Iterable[Path] = ()
    ) -> list[str]:
        # --unshare-pid with a fresh /proc: the host's processes (the runner holds the
        # caller's tokens in its environment) are not visible, and ending bwrap ends every
        # process inside. A directory is covered by an empty tmpfs; a file or socket by a
        # read-only /dev/null (reads give nothing); a real gh by the case's shim; the files the
        # sandbox relies on are sealed. The file system bind comes first, or it would cover the
        # rest.
        args = [
            self.bwrap,
            "--bind",
            "/",
            "/",
            "--dev",
            "/dev",
            "--proc",
            "/proc",
            "--unshare-pid",
            "--new-session",
            "--die-with-parent",
        ]
        for kind, path in self.hidden():
            args += (
                ["--tmpfs", str(path)] if kind == "dir" else ["--ro-bind", "/dev/null", str(path)]
            )
        args += self.seal_args(gh_shim, sealed)
        if gh_shim is not None:
            for real in self.gh_replaced(gh_shim):
                args += ["--ro-bind", str(gh_shim), str(real)]
        args += ["--ro-bind", str(self.hosts_file), "/etc/hosts", "--chdir", str(workdir)]
        return args

    def wrap(
        self, cmd: list[str], workdir: Path, *, gh_shim: Path, sealed: Iterable[Path]
    ) -> list[str]:
        """`cmd` under the sandbox. The shim and the sealed folders are required: a command line
        without them would leave the real gh reachable and the files the sandbox relies on
        writable."""
        return [*self.args(workdir, gh_shim, sealed), "--", *cmd]


def make_sandbox(
    folder: Path,
    environ: Mapping[str, str] | None = None,
    which=shutil.which,
    run=subprocess.run,
) -> Sandbox:
    """A Sandbox with its hosts file written in `folder` and the real gh binaries found (`run`
    asks the login shell), or SandboxError saying why not."""
    if not sys.platform.startswith("linux"):
        raise SandboxError(
            "the eval sandbox needs bubblewrap, which runs on Linux only; run the evals on "
            "Linux (or in WSL 2)"
        )
    bwrap = which("bwrap")
    if bwrap is None:
        raise SandboxError(
            "bubblewrap (bwrap) is not installed; install it (for example `apt install "
            "bubblewrap`) — every eval agent must run inside it"
        )
    environ = dict(os.environ if environ is None else environ)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    write_hosts_file(folder / "hosts")
    return Sandbox(
        bwrap,
        Path(environ.get("HOME") or Path.home()),
        folder / "hosts",
        environ,
        real_gh=real_gh_paths(environ, run),
    )


# What runs inside the sandbox. Each check returns {"name", "ok", "detail"}; a detail names
# paths, hosts and variables, never a value.


def _check(name: str, problems: list[str], ok_detail: str) -> dict:
    return {"name": name, "ok": not problems, "detail": "; ".join(problems) or ok_detail}


def check_hidden(paths: list[Path]) -> dict:
    """Each hidden location is empty, unreadable or absent."""
    readable = []
    for p in map(Path, paths):
        try:
            if p.is_dir():
                if os.listdir(p):
                    readable.append(f"{p} is readable")
            elif p.exists():
                with open(p, "rb") as f:
                    if f.read(1):
                        readable.append(f"{p} is readable")
        except OSError:  # no permission, no such device: nothing can be read
            continue
    return _check("hidden", readable, f"{len(paths)} location(s) empty, unreadable or absent")


def check_gh(gh_paths: list[str], env: Mapping[str, str], run=subprocess.run) -> dict:
    """Every gh found reports no login, both with the environment as the agent gets it and
    with gh's own configuration-folder variables removed (so it falls back to ~/.config/gh)."""
    fallback = {k: v for k, v in env.items() if k not in ("GH_CONFIG_DIR", "XDG_CONFIG_HOME")}
    problems = []
    for gh in gh_paths:
        for e in (dict(env), fallback):
            for args in (["auth", "status"], ["auth", "token"]):
                try:
                    p = run(
                        [gh, *args],
                        env=e,
                        capture_output=True,
                        stdin=subprocess.DEVNULL,
                        timeout=PROBE_GH_TIMEOUT_S,
                    )
                except subprocess.TimeoutExpired:
                    problems.append(f"{gh} {' '.join(args)} did not finish")
                    continue
                except OSError:  # cannot be run: no login to use
                    continue
                if p.returncode == 0:
                    problems.append(f"{gh} {' '.join(args)} reports a login")
    return _check("gh", problems, f"{len(gh_paths)} gh found, none reports a login")


def check_gh_shim(
    gh_paths: list[str], env: Mapping[str, str], run=subprocess.run, login_shell: bool = True
) -> dict:
    """Every way of calling gh runs the case's shim, which here is the one that looks absent
    (exit 127, "gh: command not found"): each listed path, and the gh the login shell finds —
    found again here, inside the sandbox, because it is where the user's profile may put
    another one first."""
    paths = [(gh, "") for gh in gh_paths]
    shell = shutil.which(LOGIN_SHELL, path=env.get("PATH")) if login_shell else None
    calls = []
    if shell is not None:
        found = login_shell_gh(env, run)
        if found is not None:
            paths.append((str(found), " (found by the login shell)"))
        calls.append(([shell, "-lc", "gh auth status"], "gh called from the login shell"))
    calls += [([gh, "auth", "status"], f"{gh}{how}") for gh, how in paths]
    problems = []
    for cmd, name in calls:
        try:
            p = run(
                cmd,
                env=dict(env),
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                timeout=PROBE_GH_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            problems.append(f"{name} did not finish")
            continue
        except OSError:  # cannot be run: nothing to reach
            continue
        if p.returncode != 127 or ABSENT_GH_MESSAGE not in (p.stderr or ""):
            problems.append(f"{name} is not the case's gh")
    return _check("gh-shim", problems, f"{len(calls)} way(s) of calling gh reach the case's gh")


def _connect(address: tuple[str, int]) -> None:
    with socket.socket() as s:
        s.settimeout(PROBE_CONNECT_TIMEOUT_S)
        s.connect(address)


def check_hosts(
    hosts, address: str, resolve=socket.getaddrinfo, connect=_connect, port: int = 443
) -> dict:
    """Each host resolves to `address` alone and a connection there is refused."""
    problems = []
    for host in hosts:
        try:
            found = {info[4][0] for info in resolve(host, port, type=socket.SOCK_STREAM)}
        except OSError as e:
            problems.append(f"{host} does not resolve ({e})")
            continue
        if found != {address}:
            problems.append(f"{host} resolves to {', '.join(sorted(found))}, not {address}")
            continue
        try:
            connect((address, port))
        except ConnectionRefusedError:
            continue
        except OSError as e:
            problems.append(f"{host}: connection to {address} neither accepted nor refused ({e})")
        else:
            problems.append(f"{host}: a connection to {address} was accepted")
    return _check("hosts", problems, f"{len(list(hosts))} host(s) go to {address}, refused")


def path_writable(path: Path) -> bool:
    """Whether `path` can be written: a file is opened for appending (nothing is written), a
    folder is given a new file, which is removed at once. Nothing is renamed or removed."""
    try:
        if path.is_dir():
            fd, name = tempfile.mkstemp(prefix=".sealed-probe-", dir=path)
            os.close(fd)
            os.unlink(name)
        else:
            os.close(os.open(path, os.O_WRONLY | os.O_APPEND))
    except OSError:  # read-only file system, no permission: not writable
        return False
    return True


def check_sealed(paths: Iterable[Path], writable=path_writable) -> dict:
    """Each file and folder the sandbox relies on (the hosts file, the gh shim and its
    configuration) is there and cannot be written."""
    paths = [Path(p) for p in paths]
    problems = []
    for p in paths:
        if not p.exists():
            problems.append(f"{p} is missing")
        elif writable(p):
            problems.append(f"{p} can be written")
    return _check("sealed", problems, f"{len(paths)} file(s) and folder(s) are read-only")


def check_environment(environ: Mapping[str, str]) -> dict:
    """No issue-service variable is set, other than those the runner sets on purpose."""
    bad = sorted(n for n in environ if env_name_forbidden(n) and n.upper() not in RUNNER_SET_ENV)
    return _check("environment", [f"{n} is set" for n in bad], "no issue-service variable")


def probe(spec: dict) -> list[dict]:
    """All checks, run inside the sandbox from `spec`: hidden paths, gh paths, hosts, address,
    sealed files."""
    # the gh on this process's PATH counts too: PATH is where an agent looks first
    gh_paths = list(dict.fromkeys([*spec["gh"], *map(str, gh_on_path(os.environ.get("PATH")))]))
    return [
        check_hidden([Path(p) for p in spec["hidden"]]),
        check_gh(gh_paths, os.environ),
        check_gh_shim(gh_paths, os.environ),
        check_hosts(spec["hosts"], spec["address"]),
        check_sealed(map(Path, spec["sealed"])),
        check_environment(os.environ),
    ]


def sandbox_self_test(
    sandbox: Sandbox,
    scratch: Scratch,
    base_env: Mapping[str, str],
    run=subprocess.run,
    which=shutil.which,
) -> list[dict]:
    """Run the probe inside `sandbox`, with the environment an agent would get. Returns the
    checks; any check that is not ok means the runner must not run a case. Fails closed:
    a missing or unstartable bwrap, a probe that times out and an answer that cannot be read are
    failures too."""

    def fail(detail: str) -> list[dict]:
        return [{"name": "sandbox", "ok": False, "detail": detail}]

    if which(sandbox.bwrap) is None:
        return fail(f"bubblewrap (bwrap) is not installed or not executable: {sandbox.bwrap}")
    # Every gh the sandbox is meant to cover, and those it may have missed: the known install
    # locations and every gh on the PATH, whatever `sandbox` was told.
    known = [Path(c) for c in GH_BINARY_CANDIDATES if Path(c).is_file()]
    gh_paths = list(
        dict.fromkeys(map(str, [*known, *sandbox.real_gh, *gh_on_path(base_env.get("PATH"))]))
    )
    shim, sealed = case_gh_shim(scratch), case_sealed(scratch)
    # what the command line seals, and the files inside the sealed folders that matter most
    sealed_checked = [*sandbox.sealed_paths(shim, sealed), scratch.gh_bin, scratch.gh_config]
    spec = {
        "hidden": [str(p) for p in hidden_candidates(sandbox.home, sandbox.environ)],
        "gh": gh_paths,
        "hosts": list(BLOCKED_HOSTS),
        "address": UNUSED_ADDRESS,
        "sealed": [str(p) for p in dict.fromkeys(sealed_checked)],
    }
    env = case_env({"id": "self-test"}, scratch, base=base_env)
    workdir = scratch.folder
    cmd = [sys.executable, str(Path(__file__).resolve()), "--sandbox-probe", json.dumps(spec)]
    try:
        p = run(
            sandbox.wrap(cmd, workdir, gh_shim=shim, sealed=sealed),
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            timeout=PROBE_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return fail(f"the probe did not finish within {PROBE_TIMEOUT_S} s (timed out)")
    except OSError as e:
        return fail(f"bwrap could not be started: {e}")
    try:
        checks = json.loads(p.stdout)
    except json.JSONDecodeError:
        checks = None
    valid = isinstance(checks, list) and bool(checks)
    valid = valid and all(
        isinstance(c, dict) and {"name", "ok", "detail"} <= set(c) for c in checks
    )
    if not valid or p.returncode != 0:
        lines = [ln for ln in (p.stderr or "").strip().splitlines() if ln.strip()]
        return fail(f"bwrap could not run the probe: {lines[-1] if lines else p.stdout[:200]!r}")
    return checks


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


# --- case validation ----------------------------------------------------------------------------

FAKE_KEYS = ("jira", "gh")
JIRA_FAKE_KEYS = {"issues"}
GH_FAKE_KEYS = {"login", "signed_in", "access", "issues", "labels", "first_issue", "create"}
GH_CREATE_OUTCOMES = ("ok", "error", "lost")
# A login in a case must be plainly synthetic: the prefix cannot be a real person's account.
GH_LOGIN = re.compile(r"eval-[a-z0-9]+(?:-[a-z0-9]+)*")


def _is_count(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(x, str) and x for x in value)


def _fake_issue_problems(where: str, issues: object, text_key: str, number: bool) -> list[str]:
    if not isinstance(issues, list):
        return [f"{where} issues must be a list"]
    problems = []
    for i, row in enumerate(issues, 1):
        if not isinstance(row, dict):
            problems.append(f"{where} issue {i} must be an object")
            continue
        if not isinstance(row.get(text_key), str) or not row[text_key]:
            problems.append(f"{where} issue {i} needs `{text_key}`: a non-empty string")
        n = row.get("number")
        if number and (not _is_count(n) or n < 1):
            problems.append(f"{where} issue {i} needs `number`: a positive integer")
        for key in ("labels", "components"):
            if key in row and not _string_list(row[key]):
                problems.append(f"{where} issue {i} `{key}` must be a list of strings")
    return problems


def _fakes_problems(fakes: object) -> list[str]:
    """Schema problems of `setup.fakes`: the keys the runner knows, and each fake's settings."""
    if not isinstance(fakes, dict):
        return ["setup.fakes must be an object with the keys " + " and ".join(FAKE_KEYS)]
    problems = []
    if set(fakes) - set(FAKE_KEYS):
        problems.append(f"unknown setup.fakes key(s) {sorted(set(fakes) - set(FAKE_KEYS))}")
    jira, gh = fakes.get("jira"), fakes.get("gh")
    if "jira" in fakes:
        if not isinstance(jira, dict):
            problems.append("setup.fakes.jira must be an object")
        else:
            if set(jira) - JIRA_FAKE_KEYS:
                problems.append(
                    f"unknown setup.fakes.jira key(s) {sorted(set(jira) - JIRA_FAKE_KEYS)}"
                )
            if "issues" in jira:
                problems += _fake_issue_problems(
                    "setup.fakes.jira", jira["issues"], "summary", False
                )
    if "gh" in fakes:
        if not isinstance(gh, dict):
            problems.append("setup.fakes.gh must be an object")
        else:
            problems += _gh_fake_problems(gh)
    return problems


def _gh_fake_problems(gh: dict) -> list[str]:
    problems = []
    if set(gh) - GH_FAKE_KEYS:
        problems.append(f"unknown setup.fakes.gh key(s) {sorted(set(gh) - GH_FAKE_KEYS)}")
    login = gh.get("login", "eval-colleague")
    if not isinstance(login, str) or not GH_LOGIN.fullmatch(login):
        problems.append(f"setup.fakes.gh login must be synthetic, eval-<words>: {login!r}")
    for key in ("signed_in", "access"):
        if key in gh and not isinstance(gh[key], bool):
            problems.append(f"setup.fakes.gh {key} must be true or false")
    if "labels" in gh and not _string_list(gh["labels"]):
        problems.append("setup.fakes.gh labels must be a list of label names")
    if "first_issue" in gh and (not _is_count(gh["first_issue"]) or gh["first_issue"] < 1):
        problems.append("setup.fakes.gh first_issue must be a positive integer")
    if gh.get("create", "ok") not in GH_CREATE_OUTCOMES:
        problems.append(f"setup.fakes.gh create must be one of {GH_CREATE_OUTCOMES}")
    if "issues" in gh:
        problems += _fake_issue_problems("setup.fakes.gh", gh["issues"], "title", True)
    return problems


def _fake_expectation_problems(e: dict) -> list[str]:
    """Problems of a jira_* or gh_* expectation: its keys, a regex `pattern`, an integer
    `count` for the count kinds; gh_no_write takes nothing."""
    kind = e["kind"]
    allowed = {"kind"} if kind == "gh_no_write" else {"kind", "pattern"}
    if kind.endswith("_count"):
        allowed.add("count")
    problems = []
    if set(e) - allowed:
        problems.append(f"{kind} expectation has unknown key(s) {sorted(set(e) - allowed)}")
    if "pattern" in allowed:
        pattern = e.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            problems.append(f"{kind} expectation needs a pattern")
        else:
            try:
                re.compile(pattern)
            except re.error as err:
                problems.append(f"{kind} pattern {pattern!r} is not a regex: {err}")
    if kind.endswith("_count") and not _is_count(e.get("count")):
        problems.append(f"{kind} needs `count`: an integer of at least 0, got {e.get('count')!r}")
    return problems


def _expectation_problems(e: object, fakes: dict) -> list[str]:
    """Problems of one expectation: an unknown kind, a fake kind without its fake or with bad
    keys; the expectations inside `any` are checked the same way."""
    if not isinstance(e, dict) or e.get("kind") not in KINDS:
        kind = e.get("kind") if isinstance(e, dict) else e
        return [f"unknown expectation kind {kind!r}"]
    kind = e["kind"]
    if kind == "any":
        of = e.get("of")
        if not isinstance(of, list):
            return ["any expectation needs `of`: a list of expectations"]
        return [p for x in of for p in _expectation_problems(x, fakes)]
    problems = []
    for kinds, fake in ((JIRA_KINDS, "jira"), (GH_KINDS, "gh")):
        if kind in kinds:
            if fake not in fakes:
                problems.append(f"{kind} needs setup.fakes.{fake}")
            problems += _fake_expectation_problems(e)
    return problems


def case_problems(case: dict) -> list[str]:
    """Schema problems of one case's `setup.fakes` and expectations (empty when valid). Only
    what the fakes add is checked: the other setup keys and kinds are as they always were."""
    setup = case.get("setup", {})
    problems = _fakes_problems(setup["fakes"]) if "fakes" in setup else []
    fakes = setup.get("fakes") if isinstance(setup.get("fakes"), dict) else {}
    for e in case.get("expect", []):
        problems += _expectation_problems(e, fakes)
    return problems


def load_cases(path: Path) -> list[dict]:
    """The cases in `path`; a case with schema problems is refused, naming its file."""
    cases, problems = [], []
    for p in sorted(Path(path).glob("*.json")):
        case = json.loads(p.read_text())
        cases.append(case)
        problems += [f"{p.name}: {x}" for x in case_problems(case)]
    if problems:
        raise ValueError("invalid eval case(s): " + "; ".join(problems))
    return cases


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
        # opencode 2 has no --dir: it works in the directory it starts in (run_case sets it to
        # the workdir). --standalone gives each case a private server, so nothing of a shared
        # background service (its sessions, configuration) reaches the agent under test.
        # Skills come from <workdir>/.agents/skills (prepare_workdir); --auto approves tools.
        cmd = ["opencode", "run", "--standalone", "--format", "json", "--auto"]
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


# --- fake JIRA and fake gh ----------------------------------------------------------------------

# A Python start-up and a bind take well under a second; this only waits out a loaded machine.
FAKE_STARTUP_S = 20
# How long a fake gets to exit after SIGTERM before it is killed.
FAKE_STOP_S = 10
# A fake's log is a few KB; a file past this is not read (the agent shares the temp tree).
LOG_READ_LIMIT = 64 * 1024 * 1024


class FakeStartError(RuntimeError):
    """A fake did not start: the case's agent is not run (reported as an error, not a failure)."""


@functools.cache
def fake_jira_token() -> str:
    """The one token the fake JIRA accepts, read from the fake itself so the two cannot differ."""
    spec = importlib.util.spec_from_file_location("fake_jira", FAKE_JIRA)
    assert spec and spec.loader, FAKE_JIRA
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.TOKEN


def write_gh_shim(
    bin_dir: Path,
    log: Path,
    config: Path | None = None,
    script: Path | None = None,
    python: str = sys.executable,
) -> Path:
    """A launcher named `gh` in `bin_dir` that runs the fake gh with its `--config` (none: the
    fake looks like a machine without gh) and `--log` before the caller's arguments. A shell
    script on POSIX, gh.cmd on Windows."""
    bin_dir = Path(bin_dir)
    bin_dir.mkdir(parents=True, exist_ok=True)
    # The shim runs its own copy: the script in the repository stays writable from inside the
    # sandbox, the folder holding the copy does not.
    own = bin_dir / "fake_gh.py"
    shutil.copyfile(script or FAKE_GH, own)
    words = [python, str(own)]
    words += ["--config", str(config)] if config is not None else []
    words += ["--log", str(log)]
    if os.name == "nt":
        shim = bin_dir / "gh.cmd"
        shim.write_text("@" + " ".join(f'"{w}"' for w in words) + " %*\r\n", newline="")
    else:
        shim = bin_dir / "gh"
        shim.write_text("#!/bin/sh\nexec " + " ".join(map(shlex.quote, words)) + ' "$@"\n')
        shim.chmod(0o755)
    return shim


def read_log(path: Path) -> list[dict] | None:
    """The entries (JSON lines with a `line`) of a fake's log, or None when it is not there, is
    not a plain file, or is a link: the agent shares the temp tree and could plant a link, so a
    link is never followed. A line that is not JSON is skipped."""
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        if path.is_symlink():
            return None
        fd = os.open(path, flags)
        with os.fdopen(fd, "rb") as f:
            if not stat.S_ISREG(os.fstat(f.fileno()).st_mode):
                return None
            data = f.read(LOG_READ_LIMIT)
    except OSError:
        return None
    entries = []
    for ln in data.decode("utf-8", errors="replace").splitlines():
        try:
            entry = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


class FakeServices:
    """The fakes one case asks for in `setup.fakes`, for the length of the case.

    start() launches the fake JIRA (a subprocess on a loopback port chosen by the system; its
    URL is read from its first line of output, within `startup_s`) and puts a `gh` shim in a
    private folder; stop() ends both and removes that folder. Logs go beside the transcript:
    `<name>.jira.jsonl` and `<name>.gh.jsonl`; read them with read_logs() after stop().
    Attributes are None for a fake the case does not ask for."""

    def __init__(
        self,
        case: dict,
        outdir: Path,
        name: str,
        jira_script: Path | None = None,
        startup_s: float = FAKE_STARTUP_S,
    ):
        self.config = case.get("setup", {}).get("fakes", {})
        outdir = Path(outdir)
        self.jira_log = outdir / f"{name}.jira.jsonl" if "jira" in self.config else None
        self.gh_log = outdir / f"{name}.gh.jsonl" if "gh" in self.config else None
        self.jira_url: str | None = None
        self.gh_bin: Path | None = None
        self._script = jira_script or FAKE_JIRA
        self._stderr_path = outdir / f"{name}.jira.stderr"
        self._startup_s = startup_s
        self._dir: Path | None = None
        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._stderr = None

    def start(self) -> FakeServices:
        try:
            self._dir = Path(tempfile.mkdtemp(prefix="ecmwf-eval-fakes-"))
            if self.gh_log is not None:
                self.gh_log.touch()
                config = self._dir / "gh-config.json"
                _write_json(config, self.config["gh"])
                self.gh_bin = self._dir / "bin"
                write_gh_shim(self.gh_bin, self.gh_log, config)
            if self.jira_log is not None:
                self._start_jira()
        except BaseException:
            self.stop()
            raise
        return self

    def _start_jira(self) -> None:
        assert self.jira_log is not None and self._dir is not None
        self.jira_log.touch()
        cmd = [sys.executable, str(self._script), "--log", str(self.jira_log)]
        issues = self.config["jira"].get("issues")
        if issues:
            seed = self._dir / "jira-seed.json"
            seed.write_text(json.dumps(issues))
            cmd += ["--seed", str(seed)]
        self._stderr = self._stderr_path.open("w", encoding="utf-8")
        self._proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=self._stderr, text=True, encoding="utf-8"
        )
        out = self._proc.stdout
        assert out is not None
        first: queue.Queue = queue.Queue()
        self._reader = threading.Thread(target=lambda: first.put(out.readline()), daemon=True)
        self._reader.start()
        try:
            line = first.get(timeout=self._startup_s)
        except queue.Empty:
            line = ""
        if not line.startswith("FAKE_JIRA_URL="):
            self._stderr.flush()
            why = self._stderr_path.read_text(encoding="utf-8", errors="replace").strip()
            self.stop()
            last = why.splitlines()[-1] if why else (line.strip() or "no output")
            raise FakeStartError(f"fake JIRA did not start: {last}")
        self.jira_url = line.strip().split("=", 1)[1]

    @property
    def folder(self) -> Path | None:
        """The private folder holding the gh shim, its fake script and configuration (None until
        started)."""
        return self._dir

    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def stop(self) -> None:
        """End the fake JIRA and remove the shim folder. Safe to call twice."""
        proc, self._proc = self._proc, None
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=FAKE_STOP_S)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=FAKE_STOP_S)
            if self._reader is not None:
                self._reader.join(timeout=FAKE_STOP_S)
            if proc.stdout:
                proc.stdout.close()
        if self._stderr is not None:
            self._stderr.close()
            self._stderr = None
        if self._dir is not None:
            shutil.rmtree(self._dir, ignore_errors=True)

    def read_logs(self) -> tuple[list[dict] | None, list[dict] | None]:
        """(JIRA entries, gh entries), None for a fake not asked for or whose log was not read."""
        jira = read_log(self.jira_log) if self.jira_log else None
        gh = read_log(self.gh_log) if self.gh_log else None
        return jira, gh

    def __enter__(self) -> FakeServices:
        return self.start()

    def __exit__(self, *exc) -> None:
        self.stop()


def run_case(
    agent: str,
    case: dict,
    outdir: Path,
    model: str | None,
    attempt: int,
    mcp: bool = True,
    *,
    sandbox: Sandbox,
) -> dict:
    """Run one case's agent inside `sandbox` and grade it. There is no unsandboxed path:
    main() builds the sandbox only after its self-test passed."""
    plugin = plugin_copy(Path(tempfile.mkdtemp(prefix="ecmwf-eval-plugin-")), mcp=mcp)
    servers = tuple(mcp_servers(PLUGIN)) if mcp else ()
    wd = prepare_workdir(agent, case, plugin, mcp=mcp)
    cmd = agent_command(agent, case["prompt"], wd, case, model, plugin=plugin, mcp=mcp)
    t0 = time.time()
    name = f"{case['id']}.{agent}.{attempt}"
    wants_fakes = bool(case.get("setup", {}).get("fakes"))
    fakes = FakeServices(case, outdir, name) if wants_fakes else None
    scratch_dir = Path(tempfile.mkdtemp(prefix="ecmwf-eval-isolation-"))
    absent_gh_log = outdir / f"{name}.gh-absent.jsonl"
    scratch = make_scratch(scratch_dir, absent_gh_log)
    start_error = None
    try:
        try:
            if fakes is not None:
                fakes.start()
        except FakeStartError as e:
            start_error = f"fake services did not start: {e}"
        if start_error is not None:
            raw, err, rc = "", "", -1
        else:
            try:
                p = subprocess.run(
                    sandbox.wrap(
                        cmd,
                        wd,
                        gh_shim=case_gh_shim(scratch, fakes),
                        sealed=case_sealed(scratch, fakes),
                    ),
                    cwd=wd,
                    capture_output=True,
                    text=True,
                    timeout=case.get("timeout", 900),
                    # subprocess does not update $PWD when it changes directory; opencode 2
                    # starts in $PWD, so left alone it would work in (and write into) the
                    # repository.
                    env={**case_env(case, scratch, fakes=fakes), "PWD": str(wd)},
                )
                raw, err, rc = p.stdout, p.stderr, p.returncode
            except subprocess.TimeoutExpired as e:
                raw, err, rc = (
                    (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or ""),
                    "TIMEOUT",
                    -1,
                )
    finally:
        if fakes is not None:
            fakes.stop()
        shutil.rmtree(scratch_dir, ignore_errors=True)
        if absent_gh_log.exists() and absent_gh_log.stat().st_size == 0:
            absent_gh_log.unlink()  # nobody ran `gh`: no log to keep
    # The logs are read once the fakes have stopped: nothing is written to them any more.
    jira_log, gh_log = fakes.read_logs() if fakes is not None else (None, None)
    (outdir / f"{name}.jsonl").write_text(raw)
    if err:
        (outdir / f"{name}.stderr").write_text(err)
    t = note_startup_failure(parse_transcript(agent, raw, mcp_servers=servers), rc, err)
    if start_error is not None:
        t["error"] = start_error
    if agent == "codex":
        t["code"] += codex_written_code(wd)
    results = grade(t, case["expect"], wd, jira_log=jira_log, gh_log=gh_log)
    cleanup_case(plugin, wd)
    return {
        "id": case["id"],
        "agent": agent,
        "attempt": attempt,
        "rc": rc,
        "seconds": round(time.time() - t0),
        "cost_usd": t["cost_usd"],
        "skills": sorted(t["skills"]),
        "commands": t["commands"],
        "mcp_server": bool(servers),
        "mcp_server_status": t["mcp_servers"],
        "mcp_calls": mcp_calls(t),
        "web": t["web"],
        "error": t["error"],
        "passed": t["error"] is None and all(r["ok"] for r in results),
        "results": results,
        "jira_log": str(fakes.jira_log) if fakes is not None and fakes.jira_log else None,
        "gh_log": str(fakes.gh_log) if fakes is not None and fakes.gh_log else None,
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
    detail = f" ({x['detail']})" if x.get("detail") else ""
    return f"{x['kind']}:{x.get('name') or x.get('pattern') or ''}{detail}"


def start_sandbox(folder: Path, environ: Mapping[str, str]) -> Sandbox | None:
    """The sandbox every agent runs in, after its self-test passed; None (with the reason
    printed) when there is no sandbox, and then no case may run."""
    try:
        sandbox = make_sandbox(folder, environ)
    except SandboxError as e:
        print(f"ERROR refusing to run any case: {e}", file=sys.stderr)
        return None
    scratch = make_scratch(Path(folder) / "self-test", Path(folder) / "self-test.gh.jsonl")
    checks = sandbox_self_test(sandbox, scratch, environ)
    print(f"sandbox: bubblewrap {sandbox.bwrap}; self-test before the first case:", flush=True)
    for c in checks:
        print(f"  {'ok  ' if c['ok'] else 'FAIL'} {c['name']}: {c['detail']}", flush=True)
    if not checks or not all(c["ok"] for c in checks):
        print(
            "ERROR refusing to run any case: the sandbox failed its self-test, so an agent "
            "could reach a credential or an issue service",
            file=sys.stderr,
        )
        return None
    return sandbox


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--sandbox-probe"]:  # the self-test's probe, run inside the sandbox
        print(json.dumps(probe(json.loads(argv[1]))))
        return 0
    a = parse_args(argv)

    try:
        cases = load_cases(ROOT / "evals" / "cases")
    except ValueError as e:
        print(f"ERROR {e}", file=sys.stderr)
        return 2
    if a.case:
        cases = [c for c in cases if c["id"] in a.case]
    sandbox_dir = Path(tempfile.mkdtemp(prefix="ecmwf-eval-sandbox-"))
    try:
        sandbox = start_sandbox(sandbox_dir, os.environ)
        if sandbox is None:
            return 3
        return run_all(a, cases, sandbox)
    finally:
        shutil.rmtree(sandbox_dir, ignore_errors=True)


def run_all(a: argparse.Namespace, cases: list[dict], sandbox: Sandbox) -> int:
    outdir = new_run_dir(ROOT / "evals" / "runs")
    servers = ", ".join(f"{n} {u}" for n, u in mcp_servers().items())
    print(f"MCP server: {servers if a.mcp else 'off (--no-mcp)'}", flush=True)
    summary = []
    for c in cases:
        for i in range(a.repeat):
            r = run_case(a.agent, c, outdir, a.model, i + 1, mcp=a.mcp, sandbox=sandbox)
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
