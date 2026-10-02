#!/usr/bin/env python3
"""Bump the plugin version in both manifests and every SKILL.md (they must agree first).

python3 scripts/bump_version.py --level patch     # or minor / major
python3 scripts/bump_version.py --set 1.0.0
python3 scripts/bump_version.py --check           # print the current version
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_packaging as vp  # noqa: E402


def _files(root: Path):
    plug = root / "plugins" / vp.PLUGIN
    return (
        [
            plug / ".claude-plugin" / "plugin.json",
            plug / ".codex-plugin" / "plugin.json",
        ],
        sorted(plug.glob("skills/*/SKILL.md")),
    )


def current(root: Path = ROOT) -> str:
    return json.loads(_files(root)[0][0].read_text())["version"]


def bump(root: Path = ROOT, level: str | None = None, set_to: str | None = None) -> str:
    drift = [p for p in vp.problems(root) if "version" in p]
    if drift:
        sys.exit(
            "refusing to bump — versions already disagree:\n  " + "\n  ".join(drift)
        )
    old = current(root)
    if set_to:
        if not re.fullmatch(r"\d+\.\d+\.\d+", set_to):
            sys.exit(f"--set needs MAJOR.MINOR.PATCH, got {set_to!r}")
        new = set_to
    else:
        major, minor, patch = map(int, old.split("."))
        new = {
            "major": f"{major + 1}.0.0",
            "minor": f"{major}.{minor + 1}.0",
            "patch": f"{major}.{minor}.{patch + 1}",
        }[level]
    manifests, skills = _files(root)
    for m in manifests:
        d = json.loads(m.read_text())
        d["version"] = new
        m.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    for s in skills:
        t = s.read_text()
        s.write_text(
            re.sub(
                r'(^\s+version:\s*)"[^"]*"', rf'\g<1>"{new}"', t, count=1, flags=re.M
            )
        )
    return new


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--level", choices=["patch", "minor", "major"])
    g.add_argument("--set")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args()
    print(current() if a.check else bump(level=a.level, set_to=a.set))
    return 0


if __name__ == "__main__":
    sys.exit(main())
