<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow
[Semantic Versioning](https://semver.org/) as `MAJOR.MINOR.MICRO` without a `v` prefix.

## [Unreleased]

## [0.7.1] - 2026-10-11

**Official meteograms without the MCP, full-size MCP charts.** The official ECMWF ENS meteogram
for any place comes straight from OpenCharts when the ECMWF MCP is off or down, charts saved
from the MCP are full size, and forecast questions stay on ECMWF data when MCP tools are listed.

### Fixed

- The official ECMWF ENS meteogram for a place needed the ECMWF MCP: `opencharts.py get
  opencharts_meteogram` took no place and returned OpenCharts' default point (50.02°N 0.94°W).
  It takes `--lat LAT --lon LON` (`--kind 10-day|15-day|15-day-with-climate|plume|wave`,
  `--base-time` for an earlier run) and prints the run's base time and the attribution. It
  refuses a chart for one place without coordinates, coordinates out of range, and options a
  product does not take. The other charts for one place (precipitation type, visibility,
  sub-seasonal, EFI/CDF, vertical profiles, AIFS ENS, CAMS UV index) take `--lat --lon` too;
  `options` lists a product's place and kinds.
- `ecmwf_mcp.py call … -o FILE` saved the reduced image the MCP puts in its answer; it saves the
  full-size chart, fetched only from ECMWF's chart hosts over https (the reduced image is kept
  if that fails).
- With the ECMWF MCP tools listed, Claude Haiku answered forecast questions from weather
  websites: ecmwf-open-data claims weather questions about a place before any web search, also
  when ECMWF MCP tools are listed (they give charts, not forecast values).
- The point-forecast and earthkit skills say which meteogram to use: the official ENS
  meteogram has fixed layouts (10 or 15 days, plume, wave); a chosen period or variables is
  drawn with `ekplot.py meteogram`.

### Changed

- Eval case `oc-chart` accepts the bundled MCP's `charts_get_chart` (the same official
  product); new eval case `oc-meteogram-point`.

## [0.7.0] - 2026-10-11

**ECMWF MCP.** The plugin now bundles ECMWF's MCP server (https://mcp.ecmwf.int), and the skills
use it where it helps: official charts and meteograms and the DestinE data catalogue in chat.
Forecasts and ERA5 stay on the skills' scripts (MCP 0.4.0 has no forecast or ERA5 services);
ERA5 point series now come in seconds from ECMWF's EDR service.

### Added

- The plugin bundles the ECMWF MCP server: `.mcp.json` (Claude Code, Codex) and `mcp.json`
  (portable packages). Turn it off in Claude Code via `/mcp`, in Codex with
  `[plugins."ecmwf-weather@ecmwf".mcp_servers.ecmwf] enabled = false`.
- `ecmwf_mcp.py` in ecmwf-open-data, -polytope, -cds-ads, -opencharts-wms and -destine: the MCP
  tools from the command line for clients without MCP — images saved to files, a fallback to
  the service behind the router, a clear message when the MCP does not offer a tool, and a
  BLOCKED report with ECMWF's status on outages.
- "ECMWF MCP" sections in those skills: charts, meteograms and the DestinE catalogue from the
  MCP; forecasts, ERA5, files, ensembles and processing from the scripts, also whenever the MCP
  is unreachable. Point-forecast skills name the national meteorological service and
  MeteoAlarm for official warnings, and fetch the official ENS meteogram from OpenCharts rather
  than calling their own chart official.
- `cds.py era5-point` reads ERA5 from ECMWF's OGC API EDR service: hourly point series in
  seconds on plain Python 3, without a CDS key or queue, written exactly like the CDS
  timeseries CSV. `--route auto` (default) falls back to the CDS by itself and says why
  (variables the EDR lacks, NetCDF, the most recent days, any EDR failure); `--route edr|cds`
  forces one; `--dry-run` shows the EDR requests.
- `ecmwf-destine`: every request is validated against ECMWF's DestinE data catalogue (Omnicat),
  which returns the MARS request(s) and the server that holds the data; an invalid request
  names the axis without data and lists its valid values; unreachable catalogue: offline lint,
  marked NOT validated. `destine.py options` lists an axis's valid values with labels.
  Monthly means: `request --frequency monthly` (stream `clmn`) maps parameters to their
  time-mean equivalents (167 → 228004 avg_2t).
- Evals run with the bundled MCP server by default (`make evals MCP=0` without); expectation
  kinds `mcp`, `mcp_not`, `web_not` and `any`; eval cases `mcp-*`, `destine-monthly-mean`,
  `destine-which-variables`, `cds-era5-fast`.
- `make check-endpoints` checks the MCP router's status and the DestinE catalogue. The MCP is
  optional: its outage is a warning, while drift (host gone, HTTP 404, changed status) still
  fails the release.

### Changed

- `ecmwf-destine`: default resolution `standard` (`high` on request); experiment values are
  normalised to the catalogue's (`SSP3-7.0` → `ssp3-7.0`); Extremes DT defaults to the latest
  catalogued date; `retrieve` reads `request --json` and sends the token only to DestinE
  servers.
- ecmwf-open-data claims forecast questions about a place, and ecmwf-cds-ads past-weather
  questions (how hot was …), before any web search or answer from memory.
- Relative humidity as a result comes from earthkit-meteo; thermofeel's helpers only feed its
  own indices.
- Filed ecmwf/earthkit-data#1185 (relative MARS date as a string rejected) and opened
  ecmwf/thermofeel#59 (example notes); both recorded in `docs/upstream/`.

### Fixed

- `ecmwf-destine` requested `SSP3-7.0` (the catalogue has `ssp3-7.0`), had no monthly means and
  defaulted to high-resolution hourly output, so "the July 2040 monthly mean" became 744
  high-resolution fields. Climate DT precipitation is `235055` avg_tprate.
- DestinE attribution uses the wording of the upgraded-access terms (v2.1, §3.4): "based on
  data of the European Union".
- CAMS charts and CAMS WMS layers carry the full Copernicus credit (year and disclaimer); the
  web map no longer repeats it; `opencharts.py options` no longer fails on charts without steps.
- Licence wording: every ECMWF real-time product is CC BY 4.0, operational data included; what
  is restricted is access (an entitled account or a Real-time service agreement).

## [0.6.0] - 2026-10-10

### Added

- `ecmwf-observations` skill: station observations (SYNOP, METAR) from MARS — ODB feedback
  with first-guess/analysis departures and quality flags (default, ~10 h behind real time) or
  BUFR reports (`--raw`, ~2 days); station lookup by WMO id, ICAO id or name (bundled WMO OSCAR
  index, NOAA station metadata for ICAO ids); thermal-comfort indices from observations;
  `verify` pairs point forecasts with observations (bias, MAE, RMSE per step) and draws them on
  the meteogram (`ekplot.py meteogram`) and on maps (`ekplot.py map --obs`).
- Latest METARs from NOAA Aviation Weather on request (`--latest-metar`), always labelled
  non-ECMWF — a narrow, documented exception to "ECMWF sources only".
- Thermal comfort with ECMWF's thermofeel: `ekplot.py indices` maps heat index, humidex,
  apparent temperature, wind chill, UTCI, WBGT and MRT from GRIB fields (radiation averaged
  between consecutive steps; BLOCKED report when fdir is missing, `--approximate-fdir
  erbs|disc` for a labelled demonstration from Open Data); `odpoint.py`/`ptpoint.py --indices`
  add feels-like columns (ensemble p10/p50/p90) and a meteogram panel.
- earthkit recipes: thermal-comfort indices, writing derived fields to GRIB2 and NetCDF (2-D
  lat/lon), earthkit-plots on numpy arrays; the MARS keyword reference lists the radiation
  parameters including fdir (228021).
- `mars.py check --probe` proves MARS rights with a cost-only request; `ptpoint.py --check`
  makes one tiny real request — the only proof of access to operational point data
  (`--check --offline` keeps the listing-only check); `cds.py check` verifies CDS/ADS keys.
- Eval cases for observations (`obs-*`) and thermal comfort (`tc-*`).

### Changed

- Scripts run with uv need Python ≥ 3.12, and those using SciPy need SciPy ≥ 1.16: SciPy 1.15
  (the last for Python 3.10) fails to load on macOS 27. Standard-library scripts still run on
  Python 3.9.
- `mars.py lint` accepts observation requests (`type=ob/ofb/mfb`, `obsgroup`, `filter`,
  `format=odb`) with their own rules.
- `ptpoint.py` reads credentials like polytope-client (`POLYTOPE_USER_KEY`/`_EMAIL`,
  `~/.polytope-client/config.yaml`, `POLYTOPE_KEY_PATH` or `~/.polytopeapirc`, then
  `~/.ecmwfapirc`); `cds.py` reads JSON key files too.

### Fixed

- Malformed credential files (wrong layout such as Polytope's `user_key`/`user_email` in
  `~/.ecmwfapirc`, not JSON, empty, a list, not UTF-8) and partly set `ECMWF_API_*` variables
  give a BLOCKED report naming the keys found, instead of a crash (MARS, Polytope, CDS/ADS,
  DestinE).
- Polytope "insufficient permissions" is reported as no entitlement to operational data, with
  access steps, and older runs are no longer tried; `ptpoint.py --check` no longer reports
  access because a collection is listed.
- `destine.py check` exits 4 when every data bridge rejects the token.
- `cds.py validate` marked a valid request invalid when the CDS costing service had a
  temporary error (HTTP 5xx/429); it now stays valid with a warning that the size limit could
  not be checked.
- MARS retrieve/cost without a terminal no longer fail at an interactive key prompt.

## [0.5.0] - 2026-10-09

### Added

- CARRA (Arctic, 2.5 km), CERRA and CERRA-Land (Europe, 5.5 km) and pan-CARRA regional
  reanalyses: dataset ids, request templates, point time series (CERRA-Land), limits and
  citations in `ecmwf-cds-ads` (`references/regional-reanalyses.md`), and the MARS keywords
  (`class=rr`, `origin` per product) in `ecmwf-mars`. Verified with real downloads.
- Subsetting these Lambert grids: `area` alone fails server-side (MARS cannot crop them);
  `area` + `grid` regrids to regular lat/lon and crops. `cds.py validate` accepts `grid` and
  flags `area` without `grid` when the form has no area input; `mars.py lint` does the same for
  `class=rr` and requires `origin`.
- Eval cases `cerra-area`, `carra-subset-question`, `cerra-land-point`, `mars-carra`.
- CERRA ensemble (CERRA-EDA): 10 members on a 565×565, 11 km grid, 6-hourly — CDS
  `data_type: ensemble_members`, MARS `stream=enda, number=0/to/9`; verified on both, with
  `area` + `grid` subsetting. `mars.py lint` sizes it on its own grid and requires `number`
  (without it MARS fails with "Expected 1, got 10").
- `mars.py lint` validates every keyword and value against the MARS language with ECMWF's
  pymetkit when it is installed (`uv run --no-project --with 'pymetkit>=1.19,<2' python3
  mars.py lint …`): invalid values, unknown parameters, impossible dates. Without it, lint
  says values were not checked; the stdlib checks are unchanged.

### Fixed

- Eval harness: skills are copied, not symlinked, into the agent's skill directory — agents
  searching with `find` missed symlinked skills and fell back to web search.
- An interrupted `mars.py retrieve` (Ctrl-C, or SIGTERM from a timeout) left its job queued
  or active on the ECMWF Web API — ecmwf-api-client deletes jobs only after a normal finish —
  and the user's next requests waited behind it. The job is now deleted on interruption.
- `mars.py lint` compared values case-sensitively: `class=OD` drew a false warning and
  `type=FC` skipped the forecast checks.
- `mars.py lint` gave the ERA5 licence and DOI for `class=rr` and sized its fields as O1280;
  it now names the CARRA/CERRA product, its DOI and native grid size.

### Changed

- The nine earthkit issues found while writing the recipes are filed upstream
  (earthkit-meteo #212–#214, earthkit-plots #260–#262, earthkit-transforms #133–#134,
  earthkit-utils #88); `docs/upstream/` records their URLs and the filing guide.
- Contributed the fix for an unformatted earthkit-data error message (ecmwf/earthkit-data#1181,
  merged); recorded in `docs/upstream/`.

## [0.4.0] - 2026-10-08

**earthkit first.** Agents were writing code that earthkit already provides — e.g. ensemble
mean and spread maps drawn with cartopy and `.std("member")` instead of earthkit-transforms and
earthkit-plots (ECMWF styles), or `- 273.15` instead of `units="celsius"`.

### Added

- `ecmwf-earthkit` rewritten around a "Before you write code" table (hand-written habit →
  earthkit call) covering earthkit-data, -plots, -transforms, -meteo, -geo, -hydro, -utils and
  -time; component table with install specs.
- `references/recipes.md`: verified code for every job (ensemble statistics and maps, styled
  maps, multi-panel figures, plumes, daily statistics, de-accumulation and rates,
  climatologies and anomalies, area and country means, meteorological quantities, EFI/SOT and
  CRPS, regridding, units, GRIB output, river networks, hindcast dates). **Every runnable
  Python block in every skill's references is executed by the test suite.**
- `ekplot.py ens-stats`: ensemble mean / standard deviation / percentile maps
  (earthkit-transforms + earthkit-plots), with correct labels and palette for the spread.
- Upstream contributions: when earthkit fails or lacks a feature, agents solve the task, then
  ask whether to report the bug or propose a pull request, and act only on explicit approval
  (`references/upstream.md`: issue and draft-PR procedure, reproducer and CLA rules).
- `docs/upstream/`: nine verified draft issues for earthkit-meteo, -plots, -transforms and
  -utils, each with a pinned self-contained reproducer — not filed, awaiting approval.
- Lint: every data skill routes processing and plotting to `ecmwf-earthkit`; skill examples may
  not use matplotlib, cartopy, `groupby` or `- 273.15`.
- Eval cases `ek-ensemble-maps`, `ek-humidity`, `ek-upstream-bug`, `mars-ens-plots`.
- earthkit-time (0.1.x, Emerging) allowed as the one pinned exception below 1.0.

### Changed

- Scripts use earthkit for their own computations: de-accumulation, ensemble percentiles and
  daily statistics (`odpoint.py`, `ptpoint.py`) with earthkit-transforms; meteogram
  precipitation rates via `accumulation_to_rate` and bars, maps and attribution via
  earthkit-plots (no direct matplotlib). `odpoint.py`/`ptpoint.py` now also install
  earthkit-transforms.
- Eval harness grades code written to files (Claude Write/Edit, opencode write, Codex patches
  from its session log), not only shell commands, and removes each case's skill copies after
  grading so agents can't find stale skills.

### Fixed

- `ecmwf-cds-ads` climate-normals recipe used pandas `groupby` and `- 273.15`; now
  earthkit-transforms and earthkit-utils.
- Recipes corrected after executing them: `GeoKDTree` import path, `RelativeYear` import,
  `earthkit-meteo[scores]` extra, `datetime` inputs for solar functions.

## [0.3.2] - 2026-10-04

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

[Unreleased]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.7.1...HEAD
[0.7.1]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.7.0...0.7.1
[0.7.0]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.6.0...0.7.0
[0.6.0]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.5.0...0.6.0
[0.5.0]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.4.0...0.5.0
[0.4.0]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.3.2...0.4.0
[0.3.2]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.3.1...0.3.2
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
