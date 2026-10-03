#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

"""Build upload archives in dist/.

  python3 scripts/build_dist.py

- ecmwf-weather-claude.zip — plugin directory without bin/ (claude.ai-hosted plugins reject it)
- ecmwf-weather-openai.zip — plugin directory with SKILL.md `metadata:` blocks removed (the
  OpenAI uploader treats them as misplaced interface config); source files stay unchanged
Caches, __pycache__ and generated web-map output are never included.
"""

from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = "ecmwf-weather"
EXCLUDE = re.compile(r"(^|/)(__pycache__|\.cache|\.DS_Store|\.gitkeep)(/|$)|\.pyc$")


def _strip_metadata(text: str) -> str:
    return re.sub(r"^metadata:\n(?:[ \t]+.*\n)+", "", text, count=1, flags=re.M)


def _zip(src: Path, out: Path, skip_bin: bool, strip_meta: bool) -> Path:
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(src.rglob("*")):
            rel = f.relative_to(src.parent).as_posix()
            if f.is_dir() or EXCLUDE.search(rel) or (skip_bin and f"/{PLUGIN}/bin/" in f"/{rel}"):
                continue
            if strip_meta and f.name == "SKILL.md":
                z.writestr(rel, _strip_metadata(f.read_text()))
            else:
                z.write(f, rel)
    return out


def build(root: Path = ROOT, out: Path | None = None) -> list[Path]:
    out = out or root / "dist"
    out.mkdir(parents=True, exist_ok=True)
    src = root / "plugins" / PLUGIN
    return [
        _zip(src, out / f"{PLUGIN}-claude.zip", skip_bin=True, strip_meta=False),
        _zip(src, out / f"{PLUGIN}-openai.zip", skip_bin=False, strip_meta=True),
    ]


if __name__ == "__main__":
    for p in build():
        print(f"built {p} ({p.stat().st_size // 1024} KB)")
    sys.exit(0)
