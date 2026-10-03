#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Run skill evals: a fresh, context-free agent with only this plugin loaded.

  python3 scripts/run_evals.py                         # all cases, Claude Code
  python3 scripts/run_evals.py --case od-latest-run    # one case
  python3 scripts/run_evals.py --agent codex           # portability
  python3 scripts/run_evals.py --model sonnet --repeat 3

Each case runs in a new empty temp directory. Transcripts + summary.json go to
evals/runs/<timestamp>/. See AGENTS.md -> Testing for the method.
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
KINDS = {"skill", "command", "command_not", "final", "final_not", "file", "image_size"}
SKILL_PATH_RE = re.compile(r"skills/([a-z0-9-]+)/(?:SKILL\.md|scripts/|references/)")


# --- transcripts --------------------------------------------------------------------------------


def _skills_from_text(text: str) -> set[str]:
    return set(SKILL_PATH_RE.findall(text or ""))


def parse_transcript(agent: str, raw: str) -> dict:
    t = {"skills": set(), "commands": [], "final": "", "cost_usd": None, "error": None}
    for line in raw.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if agent == "claude":
            if ev.get("type") == "assistant":
                for c in ev.get("message", {}).get("content", []):
                    if c.get("type") != "tool_use":
                        continue
                    inp = c.get("input", {})
                    if c.get("name") == "Skill":
                        t["skills"].add(str(inp.get("skill", "")).split(":")[-1])
                    elif c.get("name") == "Bash":
                        t["commands"].append(inp.get("command", ""))
                        t["skills"] |= _skills_from_text(inp.get("command", ""))
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
            elif ev.get("type") == "item.completed" and item.get("type") == "agent_message":
                t["final"] = item.get("text", "")
            elif ev.get("type") in ("error", "turn.failed"):
                t["error"] = json.dumps(ev)[:300]
    if agent == "gemini":  # one pretty-printed JSON document, possibly after banner lines
        i = raw.find("{")
        try:
            doc = json.loads(raw[i:]) if i >= 0 else {}
        except json.JSONDecodeError:
            doc = {}
        t["final"] = doc.get("response", "") or ""
        if doc.get("error"):
            t["error"] = str(doc["error"].get("message", doc["error"]))[:300]
        t["skills"] |= _skills_from_text(raw)
    return t


# --- grading ------------------------------------------------------------------------------------


def grade(t: dict, expect: list[dict], workdir: Path | None = None) -> list[dict]:
    out = []
    # Agents quote paths ("$DIR/cds.py" check) and keep script paths in shell variables
    # (S=.../mars.py; python3 $S lint) — match on the expanded command without quotes.
    cmds = expand_shell_vars("\n".join(t["commands"])).replace('"', "").replace("'", "")
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


def summary_line(results: list[dict]) -> str:
    passed = sum(r["passed"] for r in results)
    errors = sum(1 for r in results if r.get("error"))
    failed = len(results) - passed - errors
    return f"{passed}/{len(results)} passed, {failed} failed, {errors} error (agent did not run)"


def load_cases(path: Path) -> list[dict]:
    return [json.loads(p.read_text()) for p in sorted(Path(path).glob("*.json"))]


# --- running ------------------------------------------------------------------------------------


def plugin_copy(base: Path) -> Path:
    """A private copy of the plugin for one case: agents sometimes write files next to the
    scripts they run, and must never be able to modify the skills under test."""
    dst = Path(base) / PLUGIN.name
    shutil.copytree(PLUGIN, dst, ignore=shutil.ignore_patterns("__pycache__", ".cache", "*.pyc"))
    return dst


def agent_command(
    agent: str,
    prompt: str,
    workdir: Path,
    case: dict,
    model: str | None = None,
    plugin: Path = PLUGIN,
) -> list[str]:
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
    elif agent == "codex":
        cmd = [
            "codex",
            "exec",
            "--skip-git-repo-check",
            "-C",
            str(workdir),
            "--json",
            "--dangerously-bypass-approvals-and-sandbox",
            prompt,
        ]
    elif agent == "gemini":
        cmd = ["gemini", "-p", prompt, "-o", "json", "--approval-mode", "yolo"]
    else:
        raise ValueError(agent)
    if model:
        cmd[1:1] = ["--model", model] if agent != "codex" else []
        if agent == "codex":
            cmd += ["-m", model]
    return cmd


def prepare_workdir(agent: str, case: dict, plugin: Path = PLUGIN) -> Path:
    wd = Path(tempfile.mkdtemp(prefix=f"ecmwf-eval-{case['id']}-"))
    for src, dst in case.get("setup", {}).get("copy", {}).items():
        shutil.copy(ROOT / src, wd / dst)
    if agent in ("codex", "gemini"):
        d = wd / (".agents/skills" if agent == "codex" else ".gemini/skills")
        d.mkdir(parents=True)
        for s in (plugin / "skills").iterdir():
            if (s / "SKILL.md").exists():
                (d / s.name).symlink_to(s)
    return wd


def run_case(agent: str, case: dict, outdir: Path, model: str | None, attempt: int) -> dict:
    plugin = plugin_copy(Path(tempfile.mkdtemp(prefix="ecmwf-eval-plugin-")))
    wd = prepare_workdir(agent, case, plugin)
    cmd = agent_command(agent, case["prompt"], wd, case, model, plugin=plugin)
    t0 = time.time()
    try:
        p = subprocess.run(
            cmd,
            cwd=wd,
            capture_output=True,
            text=True,
            timeout=case.get("timeout", 900),
            env={**os.environ},
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
    t = parse_transcript(agent, raw)
    results = grade(t, case["expect"], wd)
    return {
        "id": case["id"],
        "agent": agent,
        "attempt": attempt,
        "rc": rc,
        "seconds": round(time.time() - t0),
        "cost_usd": t["cost_usd"],
        "skills": sorted(t["skills"]),
        "error": t["error"],
        "passed": t["error"] is None and all(r["ok"] for r in results),
        "results": results,
        "workdir": str(wd),
        "final": t["final"][-1500:],
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--agent", default="claude", choices=["claude", "codex", "gemini"])
    ap.add_argument("--case", action="append", help="case id (repeatable)")
    ap.add_argument("--model")
    ap.add_argument("--repeat", type=int, default=1)
    a = ap.parse_args(argv)

    cases = load_cases(ROOT / "evals" / "cases")
    if a.case:
        cases = [c for c in cases if c["id"] in a.case]
    outdir = ROOT / "evals" / "runs" / time.strftime("%Y%m%d-%H%M%S")
    outdir.mkdir(parents=True)
    summary = []
    for c in cases:
        for i in range(a.repeat):
            r = run_case(a.agent, c, outdir, a.model, i + 1)
            summary.append(r)
            mark = "PASS" if r["passed"] else ("ERROR" if r["error"] else "FAIL")
            if r["error"]:
                print(
                    f"ERROR {c['id']} [{a.agent}#{i + 1}] agent did not run: {r['error']}",
                    flush=True,
                )
                continue
            fails = [
                f"{x['kind']}:{x.get('name') or x.get('pattern')}"
                for x in r["results"]
                if not x["ok"]
            ]
            print(
                f"{mark} {c['id']} [{a.agent}#{i + 1}] {r['seconds']}s ${r['cost_usd']} "
                f"skills={r['skills']} {'failed=' + str(fails) if fails else ''}",
                flush=True,
            )
    (outdir / "summary.json").write_text(json.dumps(summary, indent=2))
    n = sum(r["passed"] for r in summary)
    print(f"\n{summary_line(summary)} — {outdir}")
    return 0 if n == len(summary) else 1


if __name__ == "__main__":
    sys.exit(main())
