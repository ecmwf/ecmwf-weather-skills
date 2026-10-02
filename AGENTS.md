# ECMWF Weather — agent conventions

Agent Skills for ECMWF data. Plan: `PLAN.md`. Roadmap: `TODO.md`.

## Layout

- Skills live only in `plugins/ecmwf-weather/skills/<name>/` (`SKILL.md`, `references/`, `scripts/`).
  There is exactly one copy; both plugin manifests point at it.
- Repo tooling lives in `scripts/`.

## Rules

- **Never rename the plugin** `ecmwf-weather` — it keys installs and namespaces the skills.
- **Keep the two manifests in sync** — `name`, `version`, `description`, `author`, `homepage`,
  `license`, `keywords` must match in `.claude-plugin/plugin.json` and `.codex-plugin/plugin.json`;
  `metadata.version` in every `SKILL.md` matches too.
- **SKILL.md frontmatter** follows the Agent Skills spec: `name` equals the directory name; version
  under `metadata`. Descriptions must not contain `: ` (breaks YAML) — use an em dash.
- **Provider-neutral content** — don't name a specific agent's tools; say what to do, not which tool.
- **Each skill is self-contained** — repeat credential detection, Open Data fallback note, and
  attribution in every skill.
- **Default model is IFS HRES** unless the user asks otherwise.
- **earthkit first.** Use earthkit components (≥ 1.0 only) for their assigned purpose — map in
  `docs/research/earthkit-components.md`. Never install the `earthkit` meta-package or
  `earthkit-data[all]`; each script declares only the bundle it needs in PEP 723 metadata and is run
  with `uv run`. Pin `>=1`. Stdlib-only scripts exist solely as the fallback (no uv, no wheels e.g.
  Windows, or the user declines installs) and must say what the fallback cannot do.
- **Never print secrets**; redact keys in any echoed URL or request.
- **Generated references are never hand-edited** — regenerate them (see `PLAN.md` §6).
- **Don't hardcode catalog counts** in prose (parameters, layers, streams change upstream).
- **Trust live services over docs** — ECMWF docs lag (e.g. post-50r1 streams). Re-run the research
  step (`PLAN.md` §0) before each minor release.

## Testing (TDD)

Write the failing test first, then the code, then the skill text. Three layers:

### 1. Unit tests — offline, fast, every change

```bash
uv run pytest                                     # offline, stdlib-only tests (default)
uv run pytest tests/open_data -q                  # one area
uv run --group earthkit pytest -m earthkit        # offline tests that decode GRIB with earthkit
```

- Live in `tests/<skill>/test_*.py`; scripts are imported by path (`tests/conftest.py::load_script`).
- Use recorded fixtures in `tests/fixtures/` (real `.index` files, trimmed directory listings, a
  52 KB 5°-regridded IFS GRIB). Network calls are injected (`exists=`) so tests never hit ECMWF.
- Scripts must import without earthkit (import earthkit inside functions) so their pure helpers are
  testable in the default group.
- The `earthkit` dependency group in `pyproject.toml` mirrors the scripts' PEP 723 bundles — keep it
  in sync when a script's dependencies change.
- Scripts emitting `--json` must keep stdout clean: library banners (earthkit/ecmwf-opendata licence
  notices) are redirected to stderr. A `--json` CLI test guards this.

### 2. Live tests — real ECMWF services, before release / weekly CI

```bash
uv run pytest -m live                             # stdlib live tests
uv run --group earthkit pytest -m "earthkit or live"   # everything
```

Marked `@pytest.mark.live`; excluded by default (see `pyproject.toml`). Assert shape, not values
(latest run exists, `.index` parses, WMS returns `image/png`).

### 3. Skill evals — a fresh agent with no context

Proves the *skill text* works: an agent that has never seen this repo or conversation, with only
the plugin loaded, must pick the right skill, run the right script and give a correct answer.

```bash
python3 scripts/run_evals.py                      # all cases, Claude Code
python3 scripts/run_evals.py --case od-latest-run # one case
python3 scripts/run_evals.py --agent codex        # portability check
```

How it isolates the agent:

- Each case runs in a **new empty temp directory** (no repo files, no CLAUDE.md/AGENTS.md).
- **Claude Code**: `claude -p "<prompt>" --plugin-dir <abs>/plugins/ecmwf-weather
  --setting-sources project --output-format stream-json --verbose --permission-mode
  bypassPermissions --max-budget-usd <cap>`. `--setting-sources project` keeps user settings and
  plugins out; the temp dir has no project settings. (`--bare` would be stricter but needs
  `ANTHROPIC_API_KEY`; use it in CI.)
- **Codex**: skills symlinked into `<tmp>/.agents/skills/`, then `codex exec --skip-git-repo-check
  -C <tmp> --json "<prompt>"`.
- **Gemini CLI**: skills symlinked into `<tmp>/.gemini/skills/` (verify path at agentskills.io/clients),
  `gemini -p "<prompt>" -o json --approval-mode yolo`.

Cases are JSON in `evals/cases/*.json`:

```json
{"id": "od-latest-run", "prompt": "…", "timeout": 600, "max_budget_usd": 2,
 "expect": [
   {"kind": "skill", "name": "open-data"},
   {"kind": "command", "pattern": "odcatalog\\.py"},
   {"kind": "final", "pattern": "(?i)ECMWF"},
   {"kind": "final_not", "pattern": "scda"}]}
```

`skill` checks the transcript for skill activation (Claude stream-json `Skill` tool use, or a read
of the `SKILL.md` for other agents); `command` regex-matches executed shell commands; `final` /
`final_not` regex the final answer. Transcripts and a summary go to `evals/runs/<timestamp>/`
(gitignored). A case passes only if every expectation holds; flaky cases are re-run 3× and must
pass 2/3.

Result states: `PASS`, `FAIL` (skill/script problem — fix the skill), `ERROR` (the agent never
ran — auth, quota, CLI missing; not a skill result). Prerequisites: `claude` logged in (`claude
/login`, or `ANTHROPIC_API_KEY` + `--bare` in CI); `codex` logged in; `gemini` needs
`GEMINI_API_KEY`. Gemini's JSON output has no command log, so only `skill`/`final`/`file`
expectations are meaningful for it.

**Read transcripts, not just verdicts.** A PASS can hide a struggling agent: the first
`ek-meteogram` run passed but took 283 s, retried mirrors and thinned steps — which exposed a
precipitation bug and a slow fetcher. Check `seconds`, the command list
(`evals/runs/<ts>/<case>.<agent>.<n>.jsonl`) and any produced files (`workdir` in `summary.json`).

Evals cost money and take minutes — run them when `SKILL.md`, `references/` or script CLIs change,
not on every edit. Record pass/fail per case in the PR description.
