<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Reporting earthkit bugs and contributing features

**Nothing here happens without the user's explicit "yes" for that specific issue or PR.**
Ask, show the full draft, wait for approval, then act. Never file in the background, never
batch several reports under one approval, never include credentials, private paths or
non-public data.

## Contents
- When to offer
- Bug report
- Feature contribution (pull request)
- Repositories

## When to offer

- **Bug**: an earthkit function raises, returns wrong values, or contradicts its docs — and you
  have reproduced it in a minimal script with the latest release. First give the user a working
  workaround; then ask once: "This looks like a bug in earthkit-X — shall I report it?"
- **Missing functionality**: the task needed code earthkit doesn't provide (and the recipes and
  docs confirm it). After solving the task, ask once: "earthkit-X has no function for this —
  would you like me to propose one upstream as a pull request?"
- Search existing issues first (`gh issue list -R ecmwf/earthkit-X --search "..." --state all`);
  if one exists, offer to add a comment with your reproducer instead.

## Bug report

Draft, show it, and on approval file it:

```bash
gh issue create -R ecmwf/earthkit-meteo --title "wet_bulb_temperature_from_dewpoint: default bisect fails on 2-D input" --body-file issue.md
```

Without `gh` (or not logged in), give the user the prefilled URL
`https://github.com/ecmwf/earthkit-meteo/issues/new?title=...&body=...` (URL-encoded) or the
`issue.md` text to paste at `https://github.com/ecmwf/earthkit-meteo/issues/new/choose`.

`issue.md` contents (ECMWF's bug-report form asks for these):

1. **What happened** and **what was expected**, one sentence each.
2. **Minimal reproducer**: a self-contained script with PEP 723 dependencies pinned to the exact
   versions, using synthetic arrays or a public sample (`earthkit.data.from_source("sample",
   ...)`) — never the user's data unless they agree it can be public.
3. **Full traceback or wrong output.**
4. **Versions**: `uv pip list | grep -i earthkit`, Python version, OS.
5. **Workaround**, if known.

## Feature contribution (pull request)

On approval, and only with `gh` available and authenticated:

```bash
gh repo fork ecmwf/earthkit-transforms --clone && cd earthkit-transforms
git switch -c feature/<short-name> origin/develop     # earthkit PRs target develop
# implement in the package's style, add a test under tests/, update the docs page
gh pr create --draft -R ecmwf/earthkit-transforms --base develop --title "..." --body-file pr.md
```

`pr.md` contents:

1. **Use case**: the real task that needed it, in two or three sentences.
2. **Proposed API** and the code.
3. **Example data and a demonstration**: a small reproducible example (synthetic or public
   sample data) showing the result.
4. **Tests added** and how to run them.
5. The ECMWF Contributor Licence Agreement declaration from the repository's PR template —
   **the user must read and accept it themselves**; never accept it on their behalf.

Open it as a **draft** and tell the user it needs their review before marking it ready. If the
change is large or the API uncertain, propose an issue (feature request) first instead.

## Repositories

| Component | Repository | Docs |
|---|---|---|
| earthkit-data | https://github.com/ecmwf/earthkit-data | https://earthkit-data.readthedocs.io |
| earthkit-plots | https://github.com/ecmwf/earthkit-plots | https://earthkit-plots.readthedocs.io |
| earthkit-meteo | https://github.com/ecmwf/earthkit-meteo | https://earthkit-meteo.readthedocs.io |
| earthkit-geo | https://github.com/ecmwf/earthkit-geo | https://earthkit-geo.readthedocs.io |
| earthkit-transforms | https://github.com/ecmwf/earthkit-transforms | https://earthkit-transforms.readthedocs.io |
| earthkit-hydro | https://github.com/ecmwf/earthkit-hydro | https://earthkit-hydro.readthedocs.io |
| earthkit-time | https://github.com/ecmwf/earthkit-time | https://earthkit-time.readthedocs.io |
| earthkit-utils | https://github.com/ecmwf/earthkit-utils | — |

Overview: https://earthkit.ecmwf.int. Default branch for contributions: `develop`.
