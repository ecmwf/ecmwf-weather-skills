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

Seven [Agent Skills](https://agentskills.io) covering ECMWF's real-time IFS and AIFS forecasts,
ERA5 reanalysis, CAMS air quality, the MARS archive, Polytope feature extraction, Destination
Earth Digital Twins and the
ecCharts/OpenCharts map services. Install them and your agent knows which service to use, how
to build the request, how to decode GRIB with [earthkit](https://github.com/ecmwf/earthkit),
and which attribution to show — without you reading the docs or hand-writing requests.

Agent Skills is an open standard: the skills work in Claude Code, OpenAI Codex, Cursor, GitHub
Copilot, VS Code, Gemini CLI and other skills-compatible agents. The repository also packages
them as plugins for Claude Code and Codex/ChatGPT.

**No credentials needed to start.** Without keys the skills use free ECMWF Open Data and public
map services. With CDS, ADS, MARS or Polytope access they switch to the faster or richer route —
and say once what a key would add.

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
- **History and climate** — ERA5 / ERA5-Land and CAMS requests validated and costed against the
  Copernicus Data Stores *before* you have a key; downloads once you do.
- **MARS archive** — request lint, size estimate, tape-friendly monthly splitting, server cost.
- **earthkit first** — decoding, regridding, derived quantities, unit conversion and plots with
  ECMWF's earthkit components (≥ 1.0), installing only the components a task needs.
- **Attribution built in** — every script prints the licence line for its data (CC BY 4.0 for
  open data, Copernicus credit for C3S/CAMS, ECMWF licence for operational data) and stamps it
  on figures.
- **Never leaks secrets** — credentials are detected by name only and redacted from output.

## The skills

| Skill | Use it for |
|---|---|
| [`open-data`](plugins/ecmwf-weather/skills/open-data/SKILL.md) | Free IFS/AIFS forecasts — latest run, fields, sizes, point forecasts |
| [`earthkit`](plugins/ecmwf-weather/skills/earthkit/SKILL.md) | Read, process and plot GRIB/NetCDF; maps and meteograms |
| [`opencharts-wms`](plugins/ecmwf-weather/skills/opencharts-wms/SKILL.md) | WMS layers, official charts, interactive web maps |
| [`polytope`](plugins/ecmwf-weather/skills/polytope/SKILL.md) | Point, ensemble, profile, area and route extraction |
| [`cds-ads`](plugins/ecmwf-weather/skills/cds-ads/SKILL.md) | ERA5, ERA5-Land, seasonal and CAMS from the Copernicus Data Stores |
| [`mars`](plugins/ecmwf-weather/skills/mars/SKILL.md) | The MARS archive via the ECMWF Web API |
| [`destine`](plugins/ecmwf-weather/skills/destine/SKILL.md) | Destination Earth Digital Twins (Climate DT, Extremes DT) via DestinE Polytope |

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

### Other skills-compatible agents

```bash
git clone https://github.com/ecmwf/ecmwf-weather-skills.git
mkdir -p ~/.agents/skills
ln -s "$PWD"/ecmwf-weather-skills/plugins/ecmwf-weather/skills/* ~/.agents/skills/
```

Use whichever skills directory your client documents — see <https://agentskills.io/clients>.

### Requirements

- [uv](https://docs.astral.sh/uv/) — scripts declare their earthkit components inline (PEP 723)
  and `uv run` installs them on first use (~64 MB to read GRIB, ~150 MB for point forecasts).
- Linux or macOS (earthkit ships eccodes as binary wheels); on Windows use WSL.
- Python 3 alone runs the catalogue, WMS, OpenCharts, CDS/ADS and MARS helpers.

## Quick start

Ask your agent, for example:

> What will the weather be like in Lisbon over the next two days? Use the free ECMWF open data.

> Using the ECMWF skills, build me a web map with the latest ECMWF forecast layers and a time
> slider, and when I click a point show a 10-day meteogram for that location.

> How uncertain is the ECMWF temperature forecast for Reading over the next 5 days?

> Prepare a validated ERA5 request for hourly 2 m temperature in Lisbon, 1991–2020.

The scripts also work on their own (paths relative to each skill):

```bash
python3 open-data/scripts/odcatalog.py latest                    # latest complete IFS run
uv run  open-data/scripts/odpoint.py --lat 38.72 --lon -9.14 --steps 0-48
uv run  earthkit/scripts/ekplot.py meteogram point.json -o meteogram.png
python3 opencharts-wms/scripts/wms.py layers --public            # keyless map layers
python3 cds-ads/scripts/cds.py era5-point --lat 38.72 --lon -9.14 \
        --start 1991-01-01 --end 2020-12-31 --dry-run            # validate + cost, no key
```

## Tested models

Every skill is evaluated with fresh agents on 21 tasks (`make evals`).

| Agent | Models | Result |
|---|---|---|
| Claude Code | Opus, Sonnet | recommended — pass all cases |
| Codex | GPT-6.1-Sol, GPT-6-Astra, GPT-6-Sol, GPT-6-Luna, GPT-5.6-Terra, GPT-5.6-Sol, GPT-5.6-Luna | recommended — pass all cases |
| Claude Code | Haiku | **not recommended** — unreliable at executing the skills' instructions (often answers without running the scripts) |

## Credentials (optional)

| Service | Environment | File |
|---|---|---|
| Open Data, public WMS, OpenCharts | — | — |
| CDS (ERA5) | `CDSAPI_URL`, `CDSAPI_KEY` | `~/.cdsapirc` |
| ADS (CAMS) | — | `~/.adsapirc` (earthkit; ADS's own docs use `~/.cdsapirc`) |
| MARS (ECMWF Web API) | `ECMWF_API_URL`, `ECMWF_API_KEY`, `ECMWF_API_EMAIL` | `~/.ecmwfapirc` |
| Polytope | `POLYTOPE_USER_KEY`, `POLYTOPE_USER_EMAIL` | `~/.polytopeapirc` (falls back to `~/.ecmwfapirc`) |
| Destination Earth (upgraded access, on request) | `DESTINE_POLYTOPE_KEY` | `~/.polytopeapirc-destine` |

Polytope and MARS access depend on your account (ECMWF Member and Co-operating States, licensed
users). Scripts check credentials by name and never print them. When a key is missing, the agent
offers step-by-step instructions to obtain it.

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
- **Copernicus (ERA5, CAMS)** — "Generated using Copernicus Climate Change Service / Atmosphere
  Monitoring Service information <year>" plus the dataset DOI.
- **Operational and MARS data** — your organisation's ECMWF licence; check before redistributing.

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
make release-prepare VERSION=0.2.0   # maintainers: see AGENTS.md "Releasing"
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

- **`plugins/ecmwf-weather/skills/`** — the seven skills (the portable payload): `SKILL.md`,
  `references/`, `scripts/`, `assets/`.
- **`plugins/ecmwf-weather/.claude-plugin/`, `.codex-plugin/`** — plugin manifests;
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
