<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/); versions follow
[Semantic Versioning](https://semver.org/) as `MAJOR.MINOR.MICRO` without a `v` prefix.

## [Unreleased]

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

[Unreleased]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.3...HEAD
[0.1.3]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.2...0.1.3
[0.1.2]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.1...0.1.2
[0.1.1]: https://github.com/ecmwf/ecmwf-weather-skills/compare/0.1.0...0.1.1
[0.1.0]: https://github.com/ecmwf/ecmwf-weather-skills/releases/tag/0.1.0
