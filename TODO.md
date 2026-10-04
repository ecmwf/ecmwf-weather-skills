<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# TODO

Accepted, open work only — remove items when done. Finished work is recorded in `CHANGELOG.md`.

## Publication

- [ ] **URGENT — before submitting to the Claude or OpenAI directories:** upload one skill to
      claude.ai (Customize → Skills; one skill folder zipped at the root, e.g. `ecmwf-open-data`) and
      try a prompt in Chat/Cowork. Their cloud sandboxes may not reach `data.ecmwf.int` and the
      other ECMWF hosts or install earthkit with `uv`; if not, most skills only produce "cannot
      reach" reports there. Record what works, and state the limitation in the listing (or
      submit only once it works).
- [ ] Directory submissions (after the test above): Anthropic — claude.ai/directory/manage
      (Plugin bundle, folder `plugins/ecmwf-weather`, ECMWF's paid organisation account);
      OpenAI — platform.openai.com/plugins (Skills only; ECMWF business verification; decide
      first whether an MCP server will ever join this plugin — it cannot be added later)

- [ ] Confirm with ECMWF: public WMS token policy and public surface layers (2t, tp)
- [ ] CI secret `ANTHROPIC_API_KEY` for the evals workflow

## Data sources

- [ ] **ECMWF Data Store (ECDS) access** — https://ecds.ecmwf.int (TIGGE, S2S forecasts and
      reforecasts): full support in `ecmwf-cds-ads` (search, describe, validate, licence check,
      retrieve) with TIGGE/S2S request templates and eval cases; same ECMWF token as CDS/ADS
      (url `https://ecds.ecmwf.int/api`). `cds.py --store ecds` already handles check, setup and
      licence steps.
- [ ] **DestinE (needs upgraded access — reminder for the maintainer):** once a DestinE token is
      set up in `~/.polytopeapirc-destine`, verify the `ecmwf-destine` request templates live
      (Climate DT on LUMI and MN5, Extremes DT), confirm token expiry behaviour, then run the
      `destine-*` evals on Claude Code (Haiku, Sonnet, Opus) and Codex (GPT-6.1-Sol …
      GPT-5.6-Luna) and add a live test
- [ ] Live-check the setup instructions end to end with a fresh ECMWF account (CDS, ADS, Web API)
- [ ] Verify `levelist=all` level counts per class against MARS

## Improvements

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
