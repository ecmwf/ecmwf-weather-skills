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
- **SKILL.md frontmatter** follows the Agent Skills spec and the authoring rules below; version
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

## Tooling

| Command | Does |
|---|---|
| `python3 scripts/validate_packaging.py` | marketplace, both manifests and every SKILL.md agree (name, version, licence, displayName) |
| `python3 scripts/check_skills.py` | best-practice lint of every skill (see next section) |
| `python3 scripts/bump_version.py --level patch\|minor\|major` / `--set X.Y.Z` | bumps all version fields together; refuses if they already disagree |
| `python3 scripts/regenerate_references.py [--check]` | rewrites the generated references from live catalogues |
| `python3 scripts/check_links.py` | probes every URL in the skills (known non-browsable ones listed with reasons) |
| `python3 scripts/build_dist.py` | `dist/ecmwf-weather-claude.zip` (no `bin/`) and `-openai.zip` (no SKILL.md `metadata:`) |
| `python3 scripts/run_evals.py` | fresh-agent skill evals (see Testing) |

- **Generated references — never hand-edit**: `open-data/references/fields.md`,
  `opencharts-wms/references/layer-catalog.md`, `earthkit/references/versions.md`. Change the
  renderer in `scripts/regenerate_references.py` instead. They omit volatile facts (valid times)
  so they only change when a catalogue does.
- **Pre-push hook**: `.githooks/pre-push` (packaging, lint, plugin validate, offline tests).
  Enable once per clone yourself with `git config core.hooksPath .githooks` — agents must not
  change git config.
- **CI** (`.github/workflows/`): `tests.yml` on every push/PR (offline + earthkit fixtures);
  `weekly.yml` (live tests without credentials, link check, reference refresh → PR with a patch
  bump); `evals.yml` (manual, Claude Code `--bare` with `ANTHROPIC_API_KEY`, choose the model).
- **Editing Python with scripts**: a formatter may reflow files after each write, so scripted
  string replacements can silently match nothing. Re-read before editing, or assert the
  replacement happened.

## Skill authoring — follow Anthropic's best practices

Always follow https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices
(last reviewed **2026-10-02**). **Re-read it before each minor release and at least monthly**;
when it changes, update this section, `scripts/check_skills.py`, and the skills, then bump the
date above.

Enforced by `python3 scripts/check_skills.py` (runs in the default `uv run pytest`):

- `name` ≤ 64 chars, lowercase/digits/hyphens, equals the directory, no "anthropic"/"claude".
- `description` ≤ 1024 chars, **third person** ("Finds…", not "Find…"/"I can…"), states what
  the skill does **and** when to use it with concrete trigger terms; no `: `, no XML tags.
- Every `SKILL.md` **starts with a short `## Contents` table of contents** listing its sections
  and its reference files.
- `SKILL.md` body < 500 lines; detail goes to `references/`, linked **one level deep** from
  `SKILL.md` (references never link other references). References > 100 lines get their own
  `## Contents`.
- No time-sensitive wording ("since May 2026…") outside a final `## Old patterns` section.
- Forward-slash paths only.

Applied by review (not mechanically checkable):

- **Concise** — assume the agent is smart; cut anything it already knows. Every paragraph must
  justify its tokens.
- **Degrees of freedom** — fragile operations (downloads, credentials, attribution) get exact
  commands; open-ended tasks get guidance.
- **Workflows** — multi-step tasks get a copyable checklist; quality-critical steps get a
  validate → fix → repeat loop.
- **Scripts** — say whether to *run* or *read* a script (default: "run them; don't read them");
  scripts handle errors themselves with actionable messages; no unexplained constants (comment
  why each tuning value has its value).
- **One default, one escape hatch** — don't offer a menu of options.
- **Consistent terminology** — e.g. always "run" (not cycle/forecast), "step", "field",
  "gridpoint", "Open Data".
- **Evaluation-driven** — write ≥ 3 eval cases for a skill *before* writing its text, then write
  the minimum text to pass them. Test with **Haiku, Sonnet and Opus**
  (`run_evals.py --model haiku|sonnet|opus`) as well as Codex.
- **Observe navigation** — read eval transcripts for files the agent never opens (cut them) or
  always opens (promote into `SKILL.md`).

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

**Lessons from evals so far** (keep adding):
- An agent that can't find the right skill will fall back to third-party weather APIs and present
  the result as ECMWF data — every skill says "ECMWF sources only", and point-forecast cases assert
  no third-party hosts are called.
- Descriptions route: if a skill's description mentions a term ("IFS ENS") but can't serve the
  task, it must point to the skill that can.
- Agents drop attribution unless told to end the answer with it.
- Grade concepts, not wording: a correct answer said "archived by month" instead of "tape", and
  agents quote paths (`"$DIR/cds.py" check`) — the grader strips shell quotes before matching.

**Current status** (2026-10-02): Codex 19/19 across all cases (after grader fixes); Claude Code
not yet run — CLI login expired on the dev machine; Gemini needs `GEMINI_API_KEY`.

**Read transcripts, not just verdicts.** A PASS can hide a struggling agent: the first
`ek-meteogram` run passed but took 283 s, retried mirrors and thinned steps — which exposed a
precipitation bug and a slow fetcher. Check `seconds`, the command list
(`evals/runs/<ts>/<case>.<agent>.<n>.jsonl`) and any produced files (`workdir` in `summary.json`).

Evals cost money and take minutes — run them when `SKILL.md`, `references/` or script CLIs change,
not on every edit. Record pass/fail per case in the PR description.
