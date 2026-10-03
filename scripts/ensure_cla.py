# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""Make sure a pull-request description carries the ECMWF CLA declaration.

The declaration's single source of truth is the "## Contributor Licence Agreement" section of
.github/PULL_REQUEST_TEMPLATE.md. GitHub only prefills the template for PRs opened in the web
UI; PRs created through the API or `gh pr create --body` skip it. The `cla` workflow runs this
script on every opened/edited PR and appends the declaration when it is missing or altered.

  python3 scripts/ensure_cla.py body.md      # prints the description with the declaration
                                             # exit 0 = unchanged, 10 = declaration appended
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
HEADING = "## Contributor Licence Agreement"
CHANGED = 10


def cla_section(template: str) -> str:
    """The heading and declaration, up to the next heading or the end of the template."""
    start = template.index(HEADING)
    nxt = template.find("\n## ", start + len(HEADING))
    return (template[start:] if nxt == -1 else template[start:nxt]).strip() + "\n"


def with_cla(body: str | None, template: str) -> str:
    body = (body or "").replace("\r\n", "\n")
    section = cla_section(template)
    if section.strip() in body:
        return body
    return (body.rstrip() + "\n\n" if body.strip() else "") + section


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    body = Path(argv[1]).read_text()
    out = with_cla(body, TEMPLATE.read_text())
    sys.stdout.write(out)
    return 0 if out == body.replace("\r\n", "\n") else CHANGED


if __name__ == "__main__":
    sys.exit(main(sys.argv))
