# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Deterministic release steps. Called by the Makefile — see AGENTS.md "Releasing".

python3 scripts/release.py preflight X.Y.Z [--allow-major]   # before preparing
python3 scripts/release.py changelog X.Y.Z [--date YYYY-MM-DD] # [Unreleased] -> [X.Y.Z]
python3 scripts/release.py publish-check X.Y.Z               # before tagging
python3 scripts/release.py notes X.Y.Z                       # release notes to stdout
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHANGELOG = ROOT / "CHANGELOG.md"
REPO = "https://github.com/ecmwf/ecmwf-weather-skills"
SEMVER = re.compile(r"\d+\.\d+\.\d+")


class ReleaseError(Exception):
    pass


def _v(s: str) -> tuple[int, ...]:
    return tuple(int(x) for x in s.split("."))


def check_version(current: str, new: str, allow_major: bool = False) -> None:
    if not SEMVER.fullmatch(new):
        raise ReleaseError(f"version must be MAJOR.MINOR.MICRO without a 'v', got {new!r}")
    if _v(new) <= _v(current):
        raise ReleaseError(f"{new} is not newer than the current version {current}")
    if _v(new)[0] != _v(current)[0] and not allow_major:
        raise ReleaseError("MAJOR bumps need the maintainer's explicit go-ahead (--allow-major)")


def _unreleased(text: str) -> tuple[int, int]:
    m = re.search(r"^## \[Unreleased\]\n", text, re.M)
    if not m:
        raise ReleaseError("CHANGELOG.md has no '## [Unreleased]' section")
    nxt = re.search(r"^## \[", text[m.end() :], re.M)
    return m.end(), m.end() + (nxt.start() if nxt else len(text) - m.end())


def move_unreleased(text: str, version: str, day: str) -> str:
    a, b = _unreleased(text)
    body = text[a:b].strip("\n")
    if not body.strip():
        raise ReleaseError("CHANGELOG.md [Unreleased] is empty — record the changes first")
    prev = re.search(r"^## \[(\d+\.\d+\.\d+)\]", text[b:], re.M)
    text = text[:a] + f"\n## [{version}] - {day}\n\n{body}\n\n" + text[b:]
    old = re.search(r"^\[Unreleased\]: .*$", text, re.M)
    links = f"[Unreleased]: {REPO}/compare/{version}...HEAD\n[{version}]: " + (
        f"{REPO}/compare/{prev.group(1)}...{version}" if prev else f"{REPO}/releases/tag/{version}"
    )
    if old:
        text = text[: old.start()] + links + text[old.end() :]
    else:
        text = text.rstrip("\n") + "\n\n" + links + "\n"
    return text


def notes(text: str, version: str) -> str:
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n", text, re.M)
    if not m:
        raise ReleaseError(f"CHANGELOG.md has no section for {version}")
    nxt = re.search(r"^## \[", text[m.end() :], re.M)
    return text[m.end() : m.end() + (nxt.start() if nxt else len(text))].strip() + "\n"


def preflight_problems(
    git: dict, changelog: str, current: str, new: str, allow_major: bool = False
) -> list[str]:
    p = []
    if git["branch"] != "main":
        p.append(f"run from 'main' (on '{git['branch']}')")
    if git["dirty"]:
        p.append("the working tree has uncommitted or untracked changes")
    if git["behind"]:
        p.append("main is not identical to origin/main — git pull first")
    if git["tag_exists"]:
        p.append(f"tag {new} already exists")
    try:
        check_version(current, new, allow_major)
    except ReleaseError as e:
        p.append(str(e))
    try:
        a, b = _unreleased(changelog)
        if not changelog[a:b].strip():
            p.append("CHANGELOG.md [Unreleased] is empty — record the changes first")
    except ReleaseError as e:
        p.append(str(e))
    return p


# --- git state ----------------------------------------------------------------------------------


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False
    ).stdout.strip()


def git_state(version: str) -> dict:
    _git("fetch", "--quiet", "--tags", "origin")
    return {
        "branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_git("status", "--porcelain")),
        "behind": _git("rev-parse", "HEAD") != _git("rev-parse", "origin/main"),
        "tag_exists": bool(_git("tag", "-l", version))
        or bool(_git("ls-remote", "--tags", "origin", f"refs/tags/{version}")),
    }


def current_version() -> str:
    import json

    return json.loads((ROOT / "plugins/ecmwf-weather/.claude-plugin/plugin.json").read_text())[
        "version"
    ]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("step", choices=["preflight", "changelog", "publish-check", "notes"])
    ap.add_argument("version")
    ap.add_argument("--date", default=date.today().isoformat())
    ap.add_argument("--allow-major", action="store_true")
    a = ap.parse_args(argv)
    text = CHANGELOG.read_text()
    try:
        if a.step == "preflight":
            probs = preflight_problems(
                git_state(a.version), text, current_version(), a.version, a.allow_major
            )
            for p in probs:
                print(f"PREFLIGHT: {p}", file=sys.stderr)
            if probs:
                return 1
            print(f"preflight ok: {current_version()} -> {a.version}")
        elif a.step == "changelog":
            CHANGELOG.write_text(move_unreleased(text, a.version, a.date))
            print(f"CHANGELOG.md: [Unreleased] -> [{a.version}] - {a.date}")
        elif a.step == "publish-check":
            g = git_state(a.version)
            probs = []
            if g["branch"] != "main" or g["dirty"] or g["behind"]:
                probs.append(
                    "be on a clean 'main' identical to origin/main (merge the release PR, "
                    "then git pull)"
                )
            if current_version() != a.version:
                probs.append(
                    f"main is at version {current_version()}, not {a.version} — is the "
                    "release PR merged?"
                )
            if g["tag_exists"]:
                probs.append(f"tag {a.version} already exists")
            notes(text, a.version)
            for p in probs:
                print(f"PUBLISH: {p}", file=sys.stderr)
            if probs:
                return 1
            print(f"ready to tag {a.version}")
        else:
            sys.stdout.write(notes(text, a.version))
    except ReleaseError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
