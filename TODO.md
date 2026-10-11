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
      OpenAI — platform.openai.com/plugins (ECMWF business verification; the plugin bundles the
      ECMWF MCP server, so submit it "With MCP" — the remote endpoint https://mcp.ecmwf.int —
      once ECMWF confirms the endpoint is a production service)

- [ ] Confirm with ECMWF: public WMS token policy and public surface layers (2t, tp)
- [ ] CI secret `ANTHROPIC_API_KEY` for the evals workflow

- [ ] Run the 21 eval cases on pi (`make evals AGENT=pi MODEL=…`) once pi has a login or API key
      on the eval machine; full opencode runs across models

## ECMWF MCP

- [ ] **Re-test access terms** of the services behind the bundled MCP — its OGC API EDR
      backend (still used directly by `cds.py era5-point`) and the DestinE data catalogue —
      once their owners confirm the access terms: each route must follow the same requirements
      as the official route for the same data (DestinE upgraded access for Digital Twin data;
      an entitled account or service agreement for real-time data beyond Open Data). MCP 0.4.0
      withdrew its forecast and ERA5 services; if they return, decide whether scripts may use
      them beyond quick answers in chat.
- [ ] **Feedback for the MCP developers** — open issues or pull requests once the repository is
      available. The forecast and ERA5 items apply if those services return (0.4.0 withdrew
      them):
  - Forecast `tp` is accumulated since the start of the run but each row reads "Total
    precipitation (mm)": summing the column overstates rain (Bologna, 48 h: 1.5 mm, summed 20.9
    mm). De-accumulate, or label it and return the amount over the requested window.
  - Only the 00 UTC runs, and only the last two, are served (at 18 UTC it still answered from
    00 UTC); serve the 06/12/18 UTC runs or state the run's age. Charts and forecast tools can
    answer from different runs — say so, or align them.
  - Pressure-level requests (e.g. `t` at 850 hPa) fail with HTTP 400 although the parameter is
    listed.
  - Units in the collection metadata: `q` and `tcw` are labelled "m"; `tcc` is declared % but
    delivered as a fraction.
  - Raw values only: wind as u/v components (no speed or direction), pressure in Pa, times in
    UTC only, no daily summaries for forecasts.
  - Response size: a chart is a 1.2 MB PNG inline (1.6 MB of JSON) — offer the image URL
    without the inline image; `catalogue_get_axes` returns 57–67 kB; the 21 tool definitions
    cost about 9,000 tokens per session.
  - Attribution: forecast `provenance.licence` is null while the text says CC-BY-4.0; add
    "© <year> ECMWF", the terms-of-use disclaimer and a note when values are converted. ERA5
    output lacks the C3S disclaimer and the dataset DOI, and says "Generated using" for daily
    statistics (modified data: "Contains modified …"). CAMS charts need the Copernicus CAMS
    credit. The Destination Earth licence label has no URL.
  - The DestinE authentication help tells users to save the DestinE token in
    `~/.polytopeapirc`, which overwrites an ECMWF Polytope key: use a separate file or
    variable.
  - The public WMS capabilities say `AccessConstraints: Registered users only`, which
    contradicts the public token.
  - When the router is down, `/status` answers HTTP 502 too, so clients cannot tell which
    services still work; keep `/status` up or publish the backend endpoints.
  - `find_service` routes "how much rain will fall in Bologna over the next three days" to the
    charts service (images, not amounts), and for "how hot was August 2003 in Paris" says the
    institute's services don't cover it — ECMWF does, through ERA5 on the Climate Data Store.
  - With a working router that has no forecast tool, Claude Haiku answered ordinary forecast
    questions from national weather websites (0/3; it used the skills when the router was down):
    the instructions' "name the authority for an official forecast" reads as "send forecast
    questions to the national service".
  - `charts_get_meteogram` returns no base time in its structured answer (the run is only in
    the image); the charts provenance says "© 2023"; `charts_find_charts` takes about 12 s.
- [ ] **Feedback for the DestinE catalogue (Omnicat) developers:**
  - An invalid value marks every axis of the selection invalid without saying which one;
    report the offending axis and its valid values.
  - Unavailable values in a list are dropped silently, and keys that are not axes of the
    selected branch (e.g. `date` under `clmn`) are ignored.
  - MARS ranges (`a/to/b`) match only the two endpoints.
  - Hours are stored with mixed types (`0000`–`0900` as text, `1000`–`2300` as numbers), so
    one selection matches only one group.
  - The AI-catalogue endpoints hide the storyline experiments and realizations 2–5.
  - The service runs on a test deployment (`catalogue-test`); publish the production URL.

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

- [ ] Generated CDS/ADS dataset catalogue (weekly CI)
- [ ] Claude.ai upload and ChatGPT plugin directory submission
- [ ] Gemini CLI eval runs (needs `GEMINI_API_KEY`)
