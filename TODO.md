# TODO

See `PLAN.md` for context and milestones.

## Now (v0.x)

- [x] Research step — earthkit components, live services (`docs/research/`)
- [x] M1 — `open-data` + `earthkit` skills, scripts, references, tests, eval cases
- [ ] M1 — run evals on **Claude Code** (blocked: CLI login expired — `claude /login`)
- [ ] M1 polish — meteogram precipitation panel x-axis not aligned with the other panels
      (earthkit-plots bar width/limits); share x-limits across panels
- [ ] M1 polish — `odcatalog.py latest` could link the ECMWF dissemination schedule so agents
      don't cite guessed URLs
- [x] M2 — `opencharts-wms` skill + flagship web map demo
- [ ] M2 polish — meteogram click takes ~20 s after the first (decoding 300 MB GRIB per point);
      keep a warm earthkit process or pre-extract a point index
- [ ] M2 polish — `opencharts.py get --valid-time` validation; regenerate `layers.md` from
      GetCapabilities weekly
- [ ] M3 — `polytope` skill + point-forecast fallback wiring
- [ ] M4 — `cds-ads` skill (ERA5, CAMS)
- [ ] M5 — `mars` skill
- [ ] M6 — tooling: `validate_packaging.py`, `check_skill_links.py`, `bump_version.py`,
      `build_dist.py`, `regenerate_references.py`, pre-push hook, CI workflows (unit + weekly
      live + evals with `--bare`)
- [ ] Confirm with ECMWF: attribution strings and logo rules per data source
- [ ] Confirm with ECMWF: public WMS token policy; ask for public surface layers (2t, tp)
- [ ] Confirm with ECMWF: Polytope access model for non-member-state users
- [ ] Plugin icon in `plugins/ecmwf-weather/assets/` + `composerIcon`/`logo` in Codex manifest
      (needs ECMWF comms approval to use the logo)
- [ ] Ask earthkit team about parallel range downloads in the `ecmwf-open-data` source; drop the
      custom parallel fetcher once available

## Later

- [ ] **ECMWF MCP server** — hosted or local server exposing direct tools (point forecast, latest run,
      map URL, ERA5 lookup) so agents can answer end-user weather questions without writing code.
      Document it in the skills but do **not** bundle it in the plugin (a bundled MCP server cannot be
      conditionally disabled).
- [ ] Route end-user "weather in X" Q&A through the MCP server once it exists
- [ ] Auto-generate CDS/ADS dataset ids and request schemas (weekly CI)
- [ ] **Destination Earth** — Climate DT / Extremes DT access (new skill or extension), credentials,
      generated catalog
- [ ] Claude.ai upload ZIP and ChatGPT plugin directory submission
- [ ] Gemini CLI eval runs (needs `GEMINI_API_KEY`)
