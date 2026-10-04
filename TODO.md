<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# TODO

Accepted, open work only. Finished work is recorded in `CHANGELOG.md`; design in `PLAN.md`.

## Before the first public release

- [ ] **DestinE (needs upgraded access — reminder for the maintainer):** once a DestinE token is
      set up in `~/.polytopeapirc-destine`, verify the `destine` request templates live
      (Climate DT on LUMI and MN5, Extremes DT), confirm token expiry behaviour, then run the
      `destine-*` evals on Claude Code (Haiku, Sonnet, Opus) and Codex (GPT-6.1-Sol …
      GPT-5.6-Luna) and add a live test
- [ ] Live-check the setup instructions end to end with a fresh ECMWF account (CDS, ADS, Web API)

- [ ] Claude Code skill evals on Haiku, Sonnet and Opus (`make evals MODEL=…`)
- [ ] Live download tests: `cds.py retrieve` / `era5-point` with CDS and ADS keys; confirm ERA5
      time-series CSV column names
- [ ] Live `mars.py retrieve` end to end; verify `levelist=all` level counts per class
- [ ] Confirm with ECMWF: attribution wording and logo use per data source; public WMS token
      policy and public surface layers (2t, tp); Polytope access for non-member-state users
- [ ] Plugin icon in `plugins/ecmwf-weather/assets/` (needs ECMWF communications approval)
- [ ] Open-sourcing checklist (ecmwf/codex `Legal/Open-Sourcing-Software.md`): Head of
      Development approval, IPR / sensitive-information audit, `open-source-audit` and security
      audits filed in `ecmwf/repo-audits`; then switch the repository to public
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
- [ ] Claude.ai upload and ChatGPT plugin directory submission
- [ ] Gemini CLI eval runs (needs `GEMINI_API_KEY`)
