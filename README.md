# ECMWF Weather — Agent Skills

> **Status: pre-release.** five skills are implemented and tested; `mars` is a stub. See `PLAN.md` and `TODO.md`.

Six [Agent Skills](https://agentskills.io) that teach coding agents how to find, request, decode and
visualise ECMWF data — real-time IFS and AIFS forecasts, ERA5 reanalysis, CAMS air quality, the MARS
archive, Polytope feature extraction, and OpenCharts/WMS map layers.

Agent Skills is an open standard, so these work in any skills-compatible agent — Claude Code, OpenAI
Codex, Cursor, GitHub Copilot, VS Code, Gemini CLI and others. The repository also packages the same
skills as plugins for Claude Code and OpenAI's ChatGPT/Codex plugin surfaces.

**No credentials needed to start.** Without any keys the skills use free ECMWF Open Data. Add CDS,
ADS, MARS or Polytope credentials and they switch to the faster or richer route automatically — and
tell you when a key would have helped.

## The skills

| Skill | Covers |
|---|---|
| `open-data` | Free IFS HRES/ENS and AIFS forecasts from ECMWF Open Data — runs, streams, parameters, download size, freshness |
| `cds-ads` | ERA5, ERA5-Land, seasonal and CAMS data from the Copernicus Climate and Atmosphere Data Stores |
| `mars` | MARS archive requests for licensed users via the ECMWF Web API |
| `polytope` | Point time series, profiles, polygons and routes without downloading global fields |
| `opencharts-wms` | OpenCharts products and WMS layers for Leaflet, MapLibre and OpenLayers |
| `earthkit` | Reading, processing and plotting GRIB/NetCDF with earthkit and eccodes |

## Install

### Claude Code

```
/plugin marketplace add ecmwf/ecmwf-weather-skill
/plugin install ecmwf-weather@ecmwf
/reload-plugins
```

For development: `claude --plugin-dir ./plugins/ecmwf-weather`.

### OpenAI Codex

```
codex plugin marketplace add ecmwf/ecmwf-weather-skill
codex plugin add ecmwf-weather@ecmwf
```

### Other skills-compatible agents

```bash
git clone https://github.com/ecmwf/ecmwf-weather-skill.git
mkdir -p ~/.agents/skills
ln -s "$PWD"/ecmwf-weather-skill/plugins/ecmwf-weather/skills/* ~/.agents/skills/
```

Symlink into whichever directory your client documents — see https://agentskills.io/clients.

## Try it

> Using the ECMWF skills, build me a web map that shows the latest ECMWF forecast temperature and
> precipitation layers, with a time slider, and when I click a point show a 10-day meteogram for
> that location.

## Credentials (optional)

| Service | Environment variables | Config file |
|---|---|---|
| Open Data | — | — |
| CDS / ADS | `CDSAPI_URL`, `CDSAPI_KEY` | `~/.cdsapirc` |
| MARS (Web API) | `ECMWF_API_URL`, `ECMWF_API_KEY`, `ECMWF_API_EMAIL` | `~/.ecmwfapirc` |
| Polytope | see `polytope` skill | `~/.polytopeapirc` |

Environment variables override files. Scripts never print secrets.

## Requirements

- [uv](https://docs.astral.sh/uv/) — scripts use ECMWF's earthkit components, declared inline and
  installed on first run. Only the components a task needs are installed (e.g. ~64 MB to read
  GRIB, ~150 MB for point forecasts), never the full earthkit stack.
- Linux or macOS (earthkit ships eccodes as binary wheels; on Windows use WSL).
- Python 3 alone is enough for the catalogue helper (`odcatalog.py`) — the fallback when earthkit
  can't be installed.

## Development

```bash
uv run pytest                                    # offline unit tests
uv run --group earthkit pytest -m "earthkit or live"   # earthkit + live ECMWF tests
python3 scripts/run_evals.py --agent codex       # fresh-agent skill evals
```

Testing method: `AGENTS.md` → Testing.

## Data licences

ECMWF Open Data is released under CC-BY-4.0; Copernicus data is under the Copernicus licence; MARS
data is under your ECMWF licence. Each skill states the attribution you must display.

## Layout

```
AGENTS.md                              conventions for agents working in this repo
PLAN.md / TODO.md                      design and roadmap
.claude-plugin/marketplace.json        marketplace catalog
scripts/                               repo tooling (validation, generation, builds)
plugins/ecmwf-weather/
├── .claude-plugin/plugin.json         Claude Code manifest
├── .codex-plugin/plugin.json          ChatGPT / Codex manifest
├── bin/                               CLI wrappers (Claude Code PATH)
└── skills/                            the six skills — the portable payload
```

## Licence

Apache-2.0 — see `LICENSE`.
