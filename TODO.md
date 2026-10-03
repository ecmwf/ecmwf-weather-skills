<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# TODO

Accepted, open work only. Finished work is recorded in `CHANGELOG.md`; design in `PLAN.md`.

## Before the first public release

- [ ] Claude Code skill evals on Haiku, Sonnet and Opus (`make evals MODEL=…`)
- [ ] Live download tests: `cds.py retrieve` / `era5-point` with CDS and ADS keys; confirm ERA5
      time-series CSV column names
- [ ] Live `mars.py retrieve` end to end; verify `levelist=all` level counts per class
- [ ] Confirm with ECMWF: attribution wording and logo use per data source; public WMS token
      policy and public surface layers (2t, tp); Polytope access for non-member-state users
- [ ] Plugin icon in `plugins/ecmwf-weather/assets/` (needs ECMWF communications approval)
- [ ] Open-sourcing checklist (ecmwf/codex `Legal/Open-Sourcing-Software.md`): Head of
      Development approval, IPR / sensitive-information audit, `open-source-audit` and security
      audits filed in `ecmwf/repo-audits`, CLA Assistant enabled on the repository
- [ ] CI secret `ANTHROPIC_API_KEY` for the evals workflow; remove the repository URL from
      `check_links.py` IGNORE once public

## Improvements

- [ ] Meteogram: align the precipitation panel x-axis with the other panels; higher-contrast
      ensemble median line; normalise precipitation to mm/h where step length changes
- [ ] Web map: use Polytope for click meteograms when `ptpoint.py --check` passes; otherwise
      keep a warm earthkit process so clicks after the first take seconds, not ~20 s
- [ ] `odcatalog.py latest`: link the ECMWF dissemination schedule
- [ ] `opencharts.py get --valid-time` validation against the product schema
- [ ] earthkit-data parallel Range downloads in the `ecmwf-open-data` source; drop the custom
      fetcher once available

## Later

- [ ] ECMWF MCP server — documented, not bundled (a bundled server cannot be disabled
      conditionally); route end-user "weather in X" questions through it
- [ ] Generated CDS/ADS dataset catalogue (weekly CI)
- [ ] Destination Earth skill — Climate DT / Extremes DT, DESP authentication, catalogue
- [ ] Claude.ai upload and ChatGPT plugin directory submission
- [ ] Gemini CLI eval runs (needs `GEMINI_API_KEY`)
