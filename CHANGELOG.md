<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow
[Semantic Versioning](https://semver.org/) as `MAJOR.MINOR.MICRO` without a `v` prefix.

## [Unreleased]

### Added

- Polytope and DestinE skills ask the servers which data collections the account may use
  (authenticated `GET /api/v1/collections`): `ptpoint.py --check` lists them in about a second
  on plain Python (previously it made a test data request under `uv`), and point requests verify
  `ecmwf-mars` first; `destine.py check` lists collections per data bridge (LUMI, MareNostrum 5,
  Leonardo) and `retrieve` verifies the target bridge before requesting data. Missing or
  rejected access produces a BLOCKED report instead of a failed request.
- `make check-endpoints` also checks the collections endpoints exist.

## [0.3.1] - 2026-10-04

### Added

- **opencode and pi support**, verified: opencode discovers all seven skills and runs them end to
  end (Claude Opus 4.8 and GPT-6-Luna); pi discovers all seven via its own loader.
- Root `package.json` making the repository a pi package:
  `pi install git:github.com/ecmwf/ecmwf-weather-skills` (previously installed 0 skills).
- README install sections for opencode and pi; harness rows in "Tested models".
- Eval harness: `--agent opencode` (`opencode run --format json`) and `--agent pi`
  (`pi --mode json`); an agent that exits without doing anything (no login or API key) is
  reported as ERROR, not as a skill failure.

### Fixed

- Scripts no longer write `__pycache__` into the installed skill when importing a sibling script
  (seen under opencode; breaks read-only installs and the never-modify-the-skill rule).

## [0.3.0] - 2026-10-04

### Changed

- **All skills are prefixed `ecmwf-`** (`ecmwf-open-data`, `ecmwf-earthkit`, `ecmwf-opencharts-wms`,
  `ecmwf-polytope`, `ecmwf-cds-ads`, `ecmwf-mars`, `ecmwf-destine`) so they don't collide with other
  skills where names are flat (`npx skills add`, `~/.agents/skills`, claude.ai uploads) and are
  unambiguous to the model. **Breaking** for flat installs: remove the old symlinks and re-link.
  Plugin installs pick it up on update (`/ecmwf-weather:ecmwf-mars` etc.).
- Codex `websiteURL` is https://www.ecmwf.int (the organisation); the repository stays the
  plugin's `homepage` and `repository`.

## [0.2.3] - 2026-10-04

Preparation for the Anthropic and OpenAI plugin directories.

### Added

- `plugins/ecmwf-weather/README.md` (the submitted folder's own README) disclosing everything
  the scripts run, every host they contact and what they send; a test keeps the host list
  complete. `plugins/ecmwf-weather/LICENSE`.
- Square ECMWF icon (`assets/ecmwf-icon.png`, 200×200) as the Codex `logo`/`composerIcon`;
  brand colour `#0069A6`; website, support, privacy and terms URLs (repository, ECMWF Support
  Portal, ECMWF privacy notice, ECMWF terms of use).
- README: install with `npx skills add ecmwf/ecmwf-weather-skills`.
- Tests for the directory requirements (no top-level `bin/`, README length, icon, manifest field
  limits, provider-neutral skill text).

### Changed

- Codex `shortDescription` shortened to the 30-character limit.

### Removed

- The empty `plugins/ecmwf-weather/bin/` (claude.ai and Cowork refuse plugins with a top-level
  `bin/`).

## [0.2.2] - 2026-10-04

### Fixed

- `cds.py validate` always states the licence result: `accepted` (confirmed on the account),
  `NOT accepted` (with acceptance steps), or `not checked` with the reason (no readable key, API
  error). Previously a confirmed licence printed nothing, so agents could not tell it from an
  unchecked one and hedged ("didn't explicitly confirm the licence is accepted"). JSON output
  adds `licences.state` and `licences.reason`; the cds-ads skill explains the three states.

## [0.2.1] - 2026-10-04

### Added

- ECMWF logo as the plugin icon (Codex `logo` / `composerIcon`), used with ECMWF approval;
  licensed separately (`LicenseRef-ECMWF-Logo`), not Apache-2.0.

### Changed

- Polytope access wording, confirmed by ECMWF: available to any authenticated ECMWF user with the
  same credentials as `~/.ecmwfapirc`; some datasets are restricted to Member and Co-operating
  States, ECMWF commercial data clients and researchers under a research licence
  (https://www.ecmwf.int/en/forecasts/datasets); not all datasets are available via Polytope.
- Attribution wording confirmed by ECMWF; IPR and sensitive-information review completed.
- `check_links.py` checks the repository URL (public from this release).
- `make fmt` refreshes SKILL.md Contents line numbers first, so a lint error elsewhere no
  longer blocks it.

## [0.2.0] - 2026-10-04

Pre-publication release: findings of the open-source and security audits
(ecmwf/repo-audits, 0.1.8) addressed.

### Security

- Every skill states that text relayed from remote services (banners, messages, errors,
  descriptions) is data, not instructions; `check_skills.py` enforces it (prompt injection).
- Server-provided links are followed only over https (OpenCharts image links, CDS/ADS form
  links, WMS redirects).
- `webmap.py serve` answers only its own loopback host and origin (DNS rebinding, cross-site
  requests); `--allow-host` for deliberate network use.
- `evals.yml`: dispatch inputs reach the shell only through validated environment variables;
  Claude Code pinned and installed without install scripts; transcripts are scanned for the API
  key before upload and kept 7 days.
- SECURITY.md points to GitHub private vulnerability reporting first, the Support Portal second.
- `main` requires one approving review.

### Fixed

- `cds.py retrieve` / `era5-point` unpack ZIP deliveries (the ERA5 time-series CSV and
  `netcdf_zip` arrive zipped and were saved unchanged under the requested name); archive paths
  are checked.
- `mars.py retrieve` converts relative dates (`date=-2`) to absolute ones, which earthkit-data
  requires.

### Removed

- `PLAN.md` (obsolete); the research procedure moved to AGENTS.md "Research step".

## [0.1.8] - 2026-10-04

### Added

- `make check-endpoints` (`scripts/check_endpoints.py`): verifies that every ECMWF endpoint the
  skills depend on still exists — the status feeds behind status.ecmwf.int and their shape, each
  mapped status component, the data-store message APIs and every service host (health is not
  checked). Retries transient failures. Run by the release and weekly CI.
- Deterministic release: `make release-prepare VERSION=X.Y.Z` (preflight, all gates including
  live tests and endpoint checks, version bump, changelog move, release PR) and
  `make release-publish VERSION=X.Y.Z` (checks, tag, GitHub release); `scripts/release.py`.
  The procedure is documented in AGENTS.md "Releasing".

### Fixed

- Makefile tests run in a clean make environment: inside `make release-prepare` the inherited
  `VERSION` made `make version` set the version instead of printing it (found by the first
  release run).

## [0.1.7] - 2026-10-04

### Added

- ECMWF service status on network failures: `ecmwf_status.py` (in every skill that calls ECMWF
  services) reads ECMWF's own status data — the components behind https://status.ecmwf.int,
  active maintenance sessions and CDS/ADS/ECDS notices — and BLOCKED reports say whether ECMWF
  reports an outage or maintenance or the problem is likely local. Covers Open Data, Web API /
  MARS, Polytope, WMS, OpenCharts, CDS, ADS, ECDS; DestinE (no status feed) links its notices.
- SKILL.md Contents list every section with its line number so agents can read ahead from the
  top of the file; `check_skills.py` verifies the numbers and `--fix` (run by `make fmt`)
  refreshes them; Contents must end within the first 50 lines.
- Opt-in Open Data download cache (`ECMWF_SKILLS_CACHE`), enabled by the Makefile for live tests
  and evals: a repeated 10-day point forecast drops from ~46 s to ~26 s. MIR cache in `.cache/`.
- Offline tests fail if they open a network connection.

### Fixed

- No Open Data run found while `data.ecmwf.int` is unreachable is reported as a network problem
  with ECMWF's status, not as missing data.
- `ek-meteogram` eval accepts skill paths without a trailing slash (`cd …/skills/open-data`).

Evaluation: Claude Sonnet 21/21, Opus 21/21, Haiku 16/21 (not recommended).

## [0.1.6] - 2026-10-04

### Added

- Every data source reports access barriers as a **BLOCKED** report — what blocks, why,
  numbered steps for the user, what the agent should do: missing or rejected keys (CDS, ADS,
  ECDS, MARS, Polytope, DestinE, WMS), dataset licences not accepted, no MARS/Polytope rights,
  no DestinE upgraded access, earthkit not installable, Open Data unreachable.
- `cds.py`: before any download, checks the account's accepted licences and blocks with
  detailed acceptance steps (log in, dataset page, "Terms of use", Accept, confirm); also on
  HTTP 403 licence errors.
- `cds.py --store ecds`: ECMWF Data Store for check, setup and licence steps (same token as
  CDS/ADS); full ECDS support is planned (TODO.md).
- `mars.py lint` sends TIGGE/S2S (`class=ti|s2`) to the ECMWF Data Store.
- Every `SKILL.md` has a "When blocked" section; `check_skills.py` enforces it. AGENTS.md:
  "When access is blocked — ALWAYS give detailed instructions".

### Fixed

- `cds.py validate` reports a request as invalid when the server's costing rejects it (it said
  "valid" for requests the server refused).
- ECDS data is credited to ECMWF under the dataset licence, not to Copernicus.

## [0.1.5] - 2026-10-04

### Added

- `cds.py validate` / `era5-point --dry-run` check, with the user's key, whether the dataset's
  licence is accepted and print the exact page to accept it
  (`…/datasets/<id>?tab=download#manage-licences`); `cds.py setup` explains the step. ERA5 uses
  the "CC-BY licence", accepted once per account.
- README: "Tested models" (recommended: Claude Opus and Sonnet, Codex GPT-6.x and GPT-5.6;
  Claude Haiku not recommended) and how to accept Copernicus licences.
- AGENTS.md: never exfiltrate, display or commit credentials; `test_no_secrets.py` blocks
  credential-shaped values in tracked files.

## [0.1.4] - 2026-10-04

### Added

- `destine` skill — Destination Earth Digital Twin data (Climate DT, Extremes DT, On-Demand
  Extremes) via DestinE Polytope: `destine.py check` (token present, never printed), `setup`
  (register, request upgraded access from the European Commission, authenticate), `request`
  (build, lint and route to the right data bridge — LUMI, MareNostrum 5 or Leonardo, optional
  point time series), `retrieve`. The token lives in `~/.polytopeapirc-destine` so it never
  overwrites an ECMWF Polytope key. Request templates follow the official examples; live
  verification is pending upgraded access.
- Missing credentials: every skill says once what a key would unlock and offers step-by-step
  instructions, given only on request — `cds.py setup [--store ads]`, `mars.py setup`,
  `ptpoint.py --setup`, `destine.py setup` (verified against the official pages, 2026-10).
- Eval cases `destine-access`, `destine-request`; eval cases can set environment variables
  (`setup.env`), used to simulate a user without a CDS key.

### Fixed

- `cds.py check` finds an ADS key in `~/.cdsapirc` (where ADS's own instructions put it) and
  says earthkit needs it in `~/.adsapirc`.
- `mars` notes that TIGGE and S2S moved to the ECMWF Data Store (ecds.ecmwf.int).
- `cds-ads` answers name the dataset id with every request.
- Eval harness: parallel runs started in the same second no longer collide.

## [0.1.3] - 2026-10-03

Fixes from the Codex evaluation across seven models (GPT-6.1-Sol, GPT-6-Astra, GPT-6-Sol,
GPT-6-Luna, GPT-5.6-Terra, GPT-5.6-Sol, GPT-5.6-Luna): all pass 19/19. Capable models were
compensating for tool gaps with their own code; the fixes below halve that.

### Added

- `odpoint.py` / `ptpoint.py`: `--next-hours N` (exactly the next N hours — runs start up to
  ~13 h before now), `--tz` (local times), `--from-now`, `--daily` (per-local-day high, low and
  precipitation; for ensembles p10/p50/p90 of each member's daily values).
- Eval harness: `image_size` expectation; `wms-getmap` checks the PNG is the size asked for.

### Fixed

- `ekplot.py meteogram`: one shared time axis for all panels (local time with `--tz` data);
  precipitation drawn as a rate in mm/h with bars spanning their interval (1/3/6-hourly totals
  were overlapping, mis-sized and not comparable); higher-contrast ensemble median.
- `ptpoint.py` uses the 06/18 UTC runs when the range fits (≤ 144 h) — up to 6 h fresher.
- `wms.py getmap` returns exactly the requested pixel size: the box is fitted to the image's
  aspect ratio (the server shrinks mismatched requests) with enough precision and margin.
- `opencharts.py get` falls back to the previous run when the newest is still being produced
  and lacks the requested step (failed for hours after every run started).
- Eval rules match executed commands, not file names in searches; either point script counts
  for the meteogram case.

## [0.1.2] - 2026-10-03

Skill-text fixes from the Claude Code evaluation. Sonnet and Opus pass all 19 eval cases twice in
a row; Haiku meets the 2-of-3 pass rule on every case; Codex passes 19/19.

### Fixed

- `earthkit` triggers for any local GRIB/NetCDF file before the agent tries xarray, cfgrib,
  `grib_ls` or `pip install` (Haiku previously drew an unrelated OpenCharts image instead of
  mapping the user's file).
- `open-data` routes web maps to `opencharts-wms` and point forecasts for users with an ECMWF
  account to `polytope`.
- `opencharts-wms` routes maps of local files to `earthkit`, builds web maps only from
  `webmap.py`, and gives the WMS endpoint with layer names.
- `mars` requires a lint-clean request in every answer and provides lint-clean ERA5 and
  operational templates (hand-written requests used CDS keywords and missed `/to/`).
- `polytope` runs the access check itself instead of asking the user to.
- Every skill tells agents to write files in the user's directory, never inside the skill
  (agents were writing request files into the installed plugin); `check_skills.py` enforces it.

### Changed

- Eval cases grade outcomes more strictly: WMS answers must include the endpoint, map answers
  must not substitute an OpenCharts image, the MARS splitting answer is graded on the
  correctness of its request.
- The CAMS PM2.5 eval accepts any CAMS PM2.5 layer (the Europe regional layer is the better
  choice for European cities).

## [0.1.1] - 2026-10-03

Fixes from the first Claude Code evaluation (Haiku, Sonnet, Opus).

### Fixed

- `mars.py lint` rejects keywords MARS does not have, with the correct one suggested
  (`level` → `levelist`; `dataset`, `format`, `variable`, `product_type` carried over from CDS).
- `mars.py lint` rejects model-level numbers on `levtype=pl`, warns when a two-date `date`
  lacks `/to/` (retrieves only those two days) and when `class` is not a common archive
  (operational IFS is `class=od`).
- `mars.py lint` only says "split" above the 75 GB per-retrieval cap; a large single month
  gets advice to shrink grid or area instead, matching the one-request-per-month rule.
- `wms.py layers` prints the WMS endpoint (`…/wms/?token=public`) needed to use the layers.
- Eval harness: commands are graded after expanding shell variables (`S=…/mars.py; $S lint`
  was graded as not linting), and each case runs against a private copy of the plugin so
  agents cannot write into the skills under test.

### Added

- `mars.py check` verifies the ECMWF Web API key in seconds (`who-am-i`) and reports the
  account id, never the key.
- `.github/CODEOWNERS`; `main` requires a pull request with passing `tests` CI checks.

### Changed

- Repository renamed to `ecmwf/ecmwf-weather-skills` (the plugin name `ecmwf-weather` is
  unchanged).
- Pull requests carry the ECMWF CLA declaration in place of CLA Assistant: the template ends
  with it and the `cla` workflow appends it to any description that lacks it.

## [0.1.0] - 2026-10-03

### Added

- `open-data` skill — latest run, steps, fields, size estimates and parallel Range downloads of
  ECMWF Open Data (`odcatalog.py`, standard library); point forecasts with earthkit
  (`odpoint.py`).
- `earthkit` skill — inspect GRIB/NetCDF (`ekinspect.py`), maps and meteograms with ensemble
  bands (`ekplot.py`); component-per-job guidance for earthkit ≥ 1.0 and 0.x → 1.x pitfalls.
- `opencharts-wms` skill — ecCharts WMS layers, times, images, legends and point values
  (`wms.py`); official OpenCharts charts (`opencharts.py`); Leaflet web map with time slider and
  click-for-meteogram server (`webmap.py`).
- `polytope` skill — access check, hourly point forecasts and 50-member ensemble percentiles via
  feature extraction (`ptpoint.py`); verified request schemas for every feature type.
- `cds-ads` skill — credential check, catalogue search, dataset description with licence and
  DOI, offline request validation with server-side costing, ERA5 point series, retrieval via
  earthkit-data (`cds.py`).
- `mars` skill — request lint, size estimate, tape-friendly monthly plan, server-side cost and
  retrieval through the ECMWF Web API (`mars.py`).
- Claude Code and Codex plugin manifests, marketplace catalogue, distribution archives.
- Test layers: offline unit tests, earthkit tests on recorded fixtures, live tests, and
  fresh-agent skill evals (`scripts/run_evals.py`, 19 cases).
- Repository tooling: skill best-practice lint, packaging validation, version bump, generated
  references from live catalogues, link check, generated skill catalogue (`docs/skills.md`).
- Top-level `Makefile` as the single entry point (`make help`, `make all`).
- ECMWF open-source compliance: `LICENSE` with intergovernmental notice, `NOTICE`,
  `CONTRIBUTORS`, SPDX/REUSE headers checked by `reuse lint`, pull-request template with the
  ECMWF Contributor Licence Agreement, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`.
- CI workflows run the `make` targets; GitHub Actions pinned to commit SHAs.

### Fixed

- Generated Open Data field catalogue is ordered deterministically and built from the latest
  complete run, so weekly regeneration only changes when the catalogue does.

[Unreleased]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.3.1...HEAD
[0.3.1]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.3.0...0.3.1
[0.3.0]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.2.3...0.3.0
[0.2.3]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.2.2...0.2.3
[0.2.2]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.2.1...0.2.2
[0.2.1]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.2.0...0.2.1
[0.2.0]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.8...0.2.0
[0.1.8]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.7...0.1.8
[0.1.7]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.6...0.1.7
[0.1.6]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.5...0.1.6
[0.1.5]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.4...0.1.5
[0.1.4]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.3...0.1.4
[0.1.3]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.2...0.1.3
[0.1.2]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.1...0.1.2
[0.1.1]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.0...0.1.1
[0.1.0]: https://github.com/ecmwf/ecmwf-weather-skills/releases/tag/0.1.0
