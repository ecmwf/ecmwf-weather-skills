<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Contributing

Thank you for helping improve the ECMWF Weather agent skills. Contributions — fixes to skill
text, new eval cases, new helper scripts, better references — are welcome. Every contribution
must come with **tests and documentation**.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) and Python ≥ 3.10
- GNU make
- Linux or macOS (earthkit wheels); Windows via WSL
- Optional: Node ≥ 20 (web-map JS tests), `claude` / `codex` CLIs (skill evals),
  ECMWF / CDS / ADS credentials (live tests of those routes)

## Quick start

```bash
git clone https://github.com/ecmwf/ecmwf-weather-skill.git
cd ecmwf-weather-skill
make setup        # dev environment + pre-push hook
make all          # the gate: lint + check + offline tests — must be green before committing
make help         # every target
```

## Workflow

1. Branch off `main`: `<type>/<kebab-summary>` with `type` one of `feat`, `fix`, `docs`,
   `chore`, `refactor`, `test`, `ci`, `perf`, `build`, `security`.
2. Write the failing test first (`tests/<skill>/`), then the code, then the skill text.
   Skill text changes need eval cases in `evals/cases/` (see `AGENTS.md` → Testing).
3. Record the user-facing change in `CHANGELOG.md` under `[Unreleased]`.
4. `make all` must pass. Run `make evals` when skill text or script CLIs change.
5. Commit with a [conventional commit](https://www.conventionalcommits.org) message
   (`feat(open-data): …`) and open a pull request against `main`; the template includes the
   CLA.

## Conventions

The rules for skill authoring, dependencies (earthkit first), credentials, attribution and
generated files are in [`AGENTS.md`](AGENTS.md) — they apply to humans and agents alike.

New files carry the SPDX header:

```
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
```

`make lint` runs `reuse lint` to check it.

## Contributor Licence Agreement

External contributors agree to the
[ECMWF Contributor Licence Agreement](https://github.com/ecmwf/codex/blob/main/Legal/Contributor-License-Agreement.md)
by ticking the box in the pull request template. Add yourself to `CONTRIBUTORS`.

## Support

This project is provided on a best-effort basis. Questions and bugs: GitHub issues. Security
issues: see [SECURITY.md](SECURITY.md).
