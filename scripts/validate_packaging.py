#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Check that marketplace, both plugin manifests, the bundled MCP server and every SKILL.md agree.

python3 scripts/validate_packaging.py        # exit 1 and list problems on drift
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "ecmwf-weather"
SHARED = ("name", "version", "description", "author", "homepage", "license", "keywords")
# The ECMWF MCP server ships with the plugin in each host's format: Claude Code reads
# `.mcp.json` (also referenced by the Codex overlay), portable Agent Plugins packages `mcp.json`.
MCP_FILES = {".mcp.json": "http", "mcp.json": "streamable-http"}
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
MCP_SCRIPT = "skills/ecmwf-open-data/scripts/ecmwf_mcp.py"


def _fm(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    fm = m.group(1) if m else ""
    out = dict(re.findall(r"^([a-z_]+):\s*(.*)$", fm, re.M))
    v = re.search(r'^\s+version:\s*"?([^"\n]+)"?', fm, re.M)
    out["metadata.version"] = v.group(1) if v else None
    return out


def mcp_problems(plug: Path, codex: dict) -> list[str]:
    """Both bundled MCP configs exist, name the same servers at the same https URLs, and the
    router is the one ecmwf_mcp.py calls when the client has no MCP tools."""
    p: list[str] = []
    servers: dict[str, dict] = {}
    for name, kind in MCP_FILES.items():
        f = plug / name
        if not f.exists():
            p.append(f"plugins/{PLUGIN}/{name} is missing (the bundled ECMWF MCP server)")
            continue
        try:
            doc = json.loads(f.read_text())
        except ValueError as e:
            p.append(f"{name} is not valid JSON: {e}")
            continue
        servers[name] = doc.get("mcpServers") or {}
        if not servers[name]:
            p.append(f"{name} declares no MCP server")
        if name == "mcp.json" and doc.get("$schema") != MCP_SCHEMA:
            p.append(f"mcp.json must declare $schema {MCP_SCHEMA}")
        for server, cfg in servers[name].items():
            if cfg.get("type") != kind:
                p.append(f"{name}: server '{server}' type {cfg.get('type')!r}, expected {kind!r}")
            if not str(cfg.get("url", "")).startswith("https://"):
                p.append(f"{name}: server '{server}' url must be https://")
    if len(servers) == len(MCP_FILES):
        claude, portable = servers[".mcp.json"], servers["mcp.json"]
        if set(claude) != set(portable):
            p.append(
                f".mcp.json and mcp.json disagree on server names: {sorted(claude)} vs "
                f"{sorted(portable)}"
            )
        for server in sorted(set(claude) & set(portable)):
            if claude[server].get("url") != portable[server].get("url"):
                p.append(
                    f"server '{server}' url differs: .mcp.json {claude[server].get('url')!r}, "
                    f"mcp.json {portable[server].get('url')!r}"
                )
        script = plug / MCP_SCRIPT
        m = re.search(r'^ROUTER = "([^"]+)"', script.read_text(), re.M) if script.exists() else None
        if "ecmwf" in claude and (m is None or m.group(1) != claude["ecmwf"].get("url")):
            p.append(f"{MCP_SCRIPT} ROUTER must be the bundled server's url")
    if codex.get("mcpServers") != "./.mcp.json":
        p.append('.codex-plugin/plugin.json must declare "mcpServers": "./.mcp.json"')
    return p


def problems(root: Path = ROOT) -> list[str]:
    p: list[str] = []
    plug = root / "plugins" / PLUGIN
    claude = json.loads((plug / ".claude-plugin" / "plugin.json").read_text())
    codex = json.loads((plug / ".codex-plugin" / "plugin.json").read_text())
    market = json.loads((root / ".claude-plugin" / "marketplace.json").read_text())

    p += mcp_problems(plug, codex)
    for k in SHARED:
        if claude.get(k) != codex.get(k):
            p.append(
                f"manifests disagree on '{k}': claude={claude.get(k)!r} codex={codex.get(k)!r}"
            )
    if claude.get("name") != PLUGIN:
        p.append(f"plugin name must stay {PLUGIN!r} (renaming breaks installs)")

    entry = next((e for e in market.get("plugins", []) if e.get("name") == PLUGIN), None)
    if entry is None:
        p.append(f"marketplace has no entry named {PLUGIN!r}")
    else:
        if (root / entry.get("source", "")).resolve() != plug.resolve():
            p.append(
                f"marketplace source {entry.get('source')!r} does not point at plugins/{PLUGIN}"
            )
        names = {
            "marketplace displayName": entry.get("displayName"),
            "claude displayName": claude.get("displayName"),
            "codex interface.displayName": codex.get("interface", {}).get("displayName"),
        }
        if len(set(names.values())) != 1:
            p.append(f"displayName differs: {names}")

    version = claude.get("version")
    for skill in sorted((plug / "skills").iterdir()):
        md = skill / "SKILL.md"
        if not md.exists():
            continue
        fm = _fm(md.read_text())
        if fm.get("name") != skill.name:
            p.append(
                f"skill {skill.name}: frontmatter name {fm.get('name')!r} must equal the directory"
            )
        if fm.get("metadata.version") != version:
            p.append(
                f"skill {skill.name}: metadata.version {fm.get('metadata.version')!r} "
                f"!= plugin version {version!r}"
            )
        if fm.get("license") != claude.get("license"):
            p.append(
                f"skill {skill.name}: license {fm.get('license')!r} != {claude.get('license')!r}"
            )
    return p


def main() -> int:
    probs = problems()
    for x in probs:
        print(x)
    print("ok" if not probs else f"{len(probs)} problem(s)")
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main())
