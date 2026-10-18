<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

[![license](https://img.shields.io/github/license/ecmwf/ecmwf-weather-skills)](https://www.apache.org/licenses/LICENSE-2.0.html)
[![tests](https://img.shields.io/github/actions/workflow/status/ecmwf/ecmwf-weather-skills/tests.yml?label=tests)](https://github.com/ecmwf/ecmwf-weather-skills/actions)
[![Agent Skills](https://img.shields.io/badge/Agent%20Skills-standard-blue)](https://agentskills.io)
[![maturity](https://github.com/ecmwf/codex/raw/refs/heads/main/Project%20Maturity/emerging_badge.svg)](https://github.com/ecmwf/codex/raw/refs/heads/main/Project%20Maturity#emerging)

# ECMWF Weather — Agent Skills

**Teach any AI coding agent to find, fetch, decode, map and cite ECMWF weather data.**

> [!IMPORTANT]
> This software is **Emerging** and subject to ECMWF's guidelines on [Software Maturity](https://github.com/ecmwf/codex/raw/refs/heads/main/Project%20Maturity).

[Agent Skills](https://agentskills.io) covering ECMWF's real-time IFS and AIFS forecasts,
ERA5 reanalysis, CAMS air quality, the MARS archive, station observations, Polytope feature
extraction, Destination Earth Digital Twins and the
ecCharts/OpenCharts map services. Install them and your agent knows which service to use, how
to build the request, how to decode GRIB with [earthkit](https://github.com/ecmwf/earthkit),
and which attribution to show — without you reading the docs or hand-writing requests.

Agent Skills is an open standard: the skills work in Claude Code, OpenAI Codex, Cursor, GitHub
Copilot, VS Code, Gemini CLI and other skills-compatible agents. The repository also packages
them as plugins for Claude Code and Codex/ChatGPT.

**No credentials needed to start.** Without keys the skills use free ECMWF Open Data, public
map services and keyless ERA5 point series. With CDS, ADS, MARS or Polytope access they switch to
the faster or richer route — and say once what a key would add.

**NOTE** This project is developed through agentic engineering: humans working in tandem with
AI coding agents, with every skill tested by fresh agents (see [Testing](#build-and-test)).

> [!WARNING]
> Not for operational use. Forecasts retrieved through these skills are raw model output, not
> official warnings — for weather warnings consult your national meteorological service.

## Features

- **Free forecasts at any point** — latest IFS (or AIFS) run, nearest gridpoint, °C, mm, m/s,
  hPa; only the needed fields are downloaded, over parallel HTTP Range requests.
- **Server-side extraction with Polytope** — hourly point forecasts and 50-member ensemble
  percentiles in ~10 KB and seconds, for users with ECMWF access.
- **Maps** — ecCharts WMS layers (pressure, geopotential, temperature and wind aloft, ensemble
  mean/spread, tropical cyclones, CAMS air quality), official OpenCharts charts, and a
  ready-made Leaflet web map with time slider and click-for-meteogram.
- **History and climate** — ERA5 hourly point series in seconds without a key (`cds.py
  era5-point`: ECMWF's EDR service, falling back to the CDS); ERA5 / ERA5-Land areas, NetCDF and
  CAMS requests validated and costed against the Copernicus Data Stores *before* you have a key;
  downloads once you do.
- **MARS archive** — request lint, size estimate, tape-friendly monthly splitting, server cost.
- **earthkit first** — agents are steered to ECMWF's earthkit components (data, plots,
  transforms, meteo, geo, hydro, utils, time) instead of hand-written numpy, matplotlib or
  cartopy: ECMWF-styled maps, ensemble and climate statistics, meteorological quantities, with
  code recipes that the test suite executes. Bugs or gaps in earthkit are offered upstream as
  issues or pull requests — only with your approval.
- **Attribution built in** — every script prints the licence line for its data (CC BY 4.0 for
  ECMWF data, Open Data and operational forecasts alike; Copernicus credit for C3S/CAMS) and
  stamps it on figures.
- **Never leaks secrets** — credentials are detected by name only and redacted from output.
- **ECMWF's MCP server bundled** — the plugins connect agents to `https://mcp.ecmwf.int` for
  official charts and meteograms and the Destination Earth catalogue in chat; the skills say
  when to use it and work without it.

## The skills

| Skill | Use it for |
|---|---|
| [`ecmwf-open-data`](plugins/ecmwf-weather/skills/ecmwf-open-data/SKILL.md) | Free IFS/AIFS forecasts — latest run, fields, sizes, point forecasts |
| [`ecmwf-earthkit`](plugins/ecmwf-weather/skills/ecmwf-earthkit/SKILL.md) | Read, process and plot GRIB/NetCDF; maps and meteograms |
| [`ecmwf-opencharts-wms`](plugins/ecmwf-weather/skills/ecmwf-opencharts-wms/SKILL.md) | WMS layers, official charts, interactive web maps |
| [`ecmwf-polytope`](plugins/ecmwf-weather/skills/ecmwf-polytope/SKILL.md) | Point, ensemble, profile, area and route extraction |
| [`ecmwf-cds-ads`](plugins/ecmwf-weather/skills/ecmwf-cds-ads/SKILL.md) | ERA5, ERA5-Land, seasonal and CAMS from the Copernicus Data Stores |
| [`ecmwf-mars`](plugins/ecmwf-weather/skills/ecmwf-mars/SKILL.md) | The MARS archive via the ECMWF Web API |
| [`ecmwf-destine`](plugins/ecmwf-weather/skills/ecmwf-destine/SKILL.md) | Destination Earth Digital Twins (Climate DT, Extremes DT) via DestinE Polytope |
| [`ecmwf-observations`](plugins/ecmwf-weather/skills/ecmwf-observations/SKILL.md) | Station observations (SYNOP, METAR) from MARS, forecast verification, observed thermal indices |

Scripts and references per skill: [`docs/skills.md`](docs/skills.md).

## Installation

### Claude Code

```
/plugin marketplace add ecmwf/ecmwf-weather-skills
/plugin install ecmwf-weather@ecmwf
/reload-plugins
```

### OpenAI Codex

```
codex plugin marketplace add ecmwf/ecmwf-weather-skills
codex plugin add ecmwf-weather@ecmwf
```

### opencode

```bash
npx skills add ecmwf/ecmwf-weather-skills -g -a opencode --skill '*' -y   # all projects
npx skills add ecmwf/ecmwf-weather-skills -a opencode --skill '*' -y      # this project only
opencode debug skill        # lists the ecmwf-* skills
```

opencode also reads `~/.claude/skills`, `~/.agents/skills` and `~/.config/opencode/skills`, so
a copy in any of them works.

### pi

```bash
pi install git:github.com/ecmwf/ecmwf-weather-skills                 # as a pi package
npx skills add ecmwf/ecmwf-weather-skills -g -a pi --skill '*' -y      # or into ~/.pi/agent/skills
```

pi runs tools without asking for approval by default; these skills download ECMWF data and run
Python scripts.

### Other skills-compatible agents

With the [skills CLI](https://skills.sh):

```bash
npx skills add ecmwf/ecmwf-weather-skills
```

Or by hand:

```bash
git clone https://github.com/ecmwf/ecmwf-weather-skills.git
mkdir -p ~/.agents/skills
ln -s "$PWD"/ecmwf-weather-skills/plugins/ecmwf-weather/skills/* ~/.agents/skills/
```

Use whichever skills directory your client documents — see <https://agentskills.io/clients>.

### Requirements

- [uv](https://docs.astral.sh/uv/) — scripts declare their earthkit components inline (PEP 723)
  and `uv run` installs only those components on first use.
- Linux or macOS (earthkit ships eccodes as binary wheels); on Windows use WSL.
- Python ≥ 3.12 for the scripts run with uv. Python 3 alone runs the catalogue, WMS, OpenCharts,
  CDS/ADS and MARS helpers.

### ECMWF MCP server

The Claude Code and Codex plugins bundle ECMWF's remote MCP server, `https://mcp.ecmwf.int`
(no account). It offers ECMWF charts and meteograms and the Destination Earth data catalogue
(the current server has no forecast or ERA5 tools): agents use its tools in chat, and the
skills call the same tools through `scripts/ecmwf_mcp.py` when the client has none. Forecasts, ERA5,
files, ensembles and everything else go through the skills' scripts, so the skills work
without it.

- Turn it off in **Claude Code**: toggle `plugin:ecmwf-weather:ecmwf` off in `/mcp`.
- Turn it off in **Codex**: in `~/.codex/config.toml`, set
  `[plugins."ecmwf-weather@ecmwf".mcp_servers.ecmwf]` `enabled = false`.
- Add it to an agent that installed only the skills: `opencode.json`
  `{"mcp": {"ecmwf": {"type": "remote", "url": "https://mcp.ecmwf.int"}}}`;
  `pi mcp add ecmwf --url https://mcp.ecmwf.int`;
  `gemini mcp add -t http ecmwf https://mcp.ecmwf.int`;
  `claude mcp add --transport http ecmwf https://mcp.ecmwf.int`;
  `codex mcp add ecmwf --url https://mcp.ecmwf.int`.

## Quick start

Ask your agent, for example:

> What will the weather be like in Lisbon over the next two days? Use the free ECMWF open data.

> Using the ECMWF skills, build me a web map with the latest ECMWF forecast layers and a time
> slider, and when I click a point show a 10-day meteogram for that location.

> How uncertain is the ECMWF temperature forecast for Reading over the next 5 days?

> Prepare a validated ERA5 request for hourly 2 m temperature in Lisbon, 1991–2020.

The scripts also work on their own (paths relative to each skill):

```bash
python3 ecmwf-open-data/scripts/odcatalog.py latest                    # latest complete IFS run
uv run  ecmwf-open-data/scripts/odpoint.py --lat 38.72 --lon -9.14 --steps 0-48
uv run  ecmwf-earthkit/scripts/ekplot.py meteogram point.json -o meteogram.png
python3 ecmwf-opencharts-wms/scripts/wms.py layers --public            # keyless map layers
python3 ecmwf-cds-ads/scripts/cds.py era5-point --lat 38.72 --lon -9.14 \
        --start 1991-01-01 --end 2020-12-31 --dry-run            # plan + cost; no key to download
```

## How the skills are tested

- **Evals.** Each case in `evals/cases/` gives one task to a fresh agent (Claude Code, Codex,
  opencode, pi or Gemini CLI) in an empty directory that has only this plugin, plus the bundled
  MCP server unless `MCP=0`. The case lists what must hold: the skill loaded, the commands run
  (and not run, such as third-party weather APIs), the files written, the MCP tools called and
  what the answer says. Run them with your agent and model; transcripts and a summary go to
  `evals/runs/<timestamp>/`:

  ```bash
  make evals AGENT=claude MODEL=sonnet                                    # every case
  make evals AGENT=codex MODEL=gpt-6.1-sol CASES="od-point-forecast oc-chart" REPEAT=3
  make evals AGENT=opencode MODEL=provider/model MCP=0                    # without the MCP server
  ```

- **Tests.** `make all` runs lint, packaging checks and the offline unit tests;
  `make test-earthkit` decodes recorded GRIB with earthkit; `make test-live` calls the real
  ECMWF services. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Credentials (optional)

| Service | Environment | File |
|---|---|---|
| Open Data, public WMS, OpenCharts | — | — |
| ERA5 hourly point series (`cds.py era5-point`) | — | — |
| CDS (ERA5 areas, NetCDF, recent days, wave parameters) | `CDSAPI_URL`, `CDSAPI_KEY` | `~/.cdsapirc` |
| ADS (CAMS) | — | `~/.adsapirc` (earthkit; ADS's own docs use `~/.cdsapirc`) |
| MARS (ECMWF Web API) | `ECMWF_API_URL`, `ECMWF_API_KEY`, `ECMWF_API_EMAIL` | `~/.ecmwfapirc` |
| Polytope | `POLYTOPE_USER_KEY`, `POLYTOPE_USER_EMAIL` | `~/.polytopeapirc` (falls back to `~/.ecmwfapirc`) |
| Destination Earth (upgraded access, on request) | `DESTINE_POLYTOPE_KEY` | `~/.polytopeapirc-destine` |

Polytope and MARS access depend on your account (users at ECMWF Member and Co-operating State
national meteorological services, or with an ECMWF service agreement). Scripts check credentials
by name and never print them. When a key is missing, the agent offers step-by-step instructions
to obtain it.

**Copernicus licences:** CDS and ADS refuse downloads (HTTP 403) until you accept the dataset's
licence once: open `https://cds.climate.copernicus.eu/datasets/<dataset-id>?tab=download#manage-licences`
while logged in, scroll to "Terms of use" and click Accept. ERA5 uses the "CC-BY licence";
accepting it once covers all CC-BY datasets. Your accepted licences are listed at
<https://cds.climate.copernicus.eu/profile?tab=licences>. One ECMWF account and token serve
CDS, ADS and the ECMWF Data Store (ECDS, TIGGE/S2S); licences are per account, so accepting a
licence on one store covers the others.

**When something blocks access** (a licence, a missing key, no entitlement, a service outage),
the scripts print a BLOCKED report with exact steps for you and for the agent.

## Data licences and attribution

Data retrieved with these skills carries its own licence, separate from this software's:

- **ECMWF Open Data, OpenCharts, public WMS** — CC BY 4.0: `© <year> ECMWF, CC BY 4.0`.
- **Copernicus (ERA5, CAMS, including CAMS charts and map layers)** — "Generated using Copernicus
  Climate Change Service / Atmosphere Monitoring Service information <year>. Neither the European
  Commission nor ECMWF is responsible for any use that may be made of the Copernicus information
  or data it contains." plus the dataset DOI.
- **Operational forecasts (Polytope, MARS)** — CC BY 4.0 too: since 1 October 2025 every product
  in ECMWF's Real-time Catalogue is CC BY 4.0 with no information cost, so credit
  `© <year> ECMWF, CC BY 4.0` with the source (www.ecmwf.int). What is restricted is access:
  beyond the free Open Data (the 0.25° subset on data.ecmwf.int) you need an ECMWF account with
  entitlement (users at Member and Co-operating State national meteorological services) or a
  Real-time service agreement (service and delivery charges). Other MARS data (observations,
  research experiments): check its terms before redistributing.

Each skill tells the agent which line to show.

## Build and test

The top-level `Makefile` is the single entry point (`make help` lists every target):

```bash
make setup          # uv environment + pre-push hook
make all            # the gate: lint + check + offline tests
make test-earthkit  # GRIB decoding tests on recorded fixtures
make test-live      # against live ECMWF services
make evals AGENT=codex   # fresh agents with only this plugin loaded must solve real tasks
make docs           # regenerate docs/skills.md
make check-endpoints      # ECMWF endpoints the skills rely on still exist
make release-prepare VERSION=X.Y.Z   # maintainers: see AGENTS.md "Releasing"
```

How the three test layers and the agent evals work: [`AGENTS.md`](AGENTS.md#testing-tdd).

## Support and community

This software is provided on a **best-effort** basis; no operational support is provided. For
questions, bug reports or feature requests, [open a GitHub issue](https://github.com/ecmwf/ecmwf-weather-skills/issues)
or a pull request. Questions about ECMWF data and services themselves go to the
[ECMWF Support Portal](https://support.ecmwf.int). Security issues: see [SECURITY.md](SECURITY.md).

Contributions — better skill text, new eval cases, fixes — are welcome; see
[CONTRIBUTING.md](CONTRIBUTING.md). Pull requests include the ECMWF Contributor Licence
Agreement.

## Documentation

- [`docs/skills.md`](docs/skills.md) — skill catalogue: scripts and references per skill
- [`AGENTS.md`](AGENTS.md) — conventions, skill-authoring rules, testing method (for humans and agents)
- [`docs/research/`](docs/research/) — service and earthkit research
- [`TODO.md`](TODO.md) — open work; [`CHANGELOG.md`](CHANGELOG.md) — release history
- [`CONTRIBUTING.md`](CONTRIBUTING.md), [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), [`SECURITY.md`](SECURITY.md)

## Repository layout

- **`plugins/ecmwf-weather/skills/`** — the skills (the portable payload): `SKILL.md`,
  `references/`, `scripts/`, `assets/`.
- **`plugins/ecmwf-weather/.claude-plugin/`, `.codex-plugin/`** — plugin manifests;
  **`plugins/ecmwf-weather/.mcp.json`, `mcp.json`** — the bundled ECMWF MCP server;
  **`.claude-plugin/marketplace.json`** — marketplace catalogue.
- **`tests/`** — unit, earthkit and live tests with recorded ECMWF fixtures.
- **`evals/cases/`** — fresh-agent skill evaluation cases.
- **`scripts/`** — repository tooling (lint, packaging, versions, generated references, evals).
- **`docs/`** — generated skill catalogue and research notes.
- **`.github/`** — CI workflows and the pull-request template.

## Copyright and Licence

Copyright 2026- European Centre for Medium-Range Weather Forecasts (ECMWF).

This software is licensed under the terms of the [Apache Licence Version 2.0](LICENSE) which can
also be obtained at http://www.apache.org/licenses/LICENSE-2.0.

In applying this licence, ECMWF does not waive the privileges and immunities granted to it by
virtue of its status as an intergovernmental organisation nor does it submit to any jurisdiction.
