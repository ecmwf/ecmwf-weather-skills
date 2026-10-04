# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
#
# Single entry point for developing this repository. `make help` lists every target.
# Every target calls the underlying tool (uv, pytest, ruff, reuse, the repo scripts) each
# time — no output-file shortcuts — so results never depend on stale local state.

.DEFAULT_GOAL := help
SHELL := /bin/bash
.SHELLFLAGS := -eu -o pipefail -c

PY      ?= python3
UV      ?= uv
PYTEST  ?= $(UV) run pytest
RUFF    ?= $(UV) run --with ruff==0.16.10 ruff
REUSE   ?= $(UV) run --with reuse==6.2.0 reuse
PLUGIN  := plugins/ecmwf-weather

# Caches so repeated runs don't re-download: Open Data byte ranges (immutable per run, pruned
# after 3 days) and MIR interpolation weights. Packages are cached by uv itself.
CACHE   ?= $(CURDIR)/.cache
export ECMWF_SKILLS_CACHE ?= $(CACHE)/data
export MIR_CACHE_PATH ?= $(CACHE)/mir

# `make version X.Y.Z` (positional) or `make version VERSION=X.Y.Z`. A VERSION inherited from
# the environment is ignored so a stray shell variable can never bump the release.
ifeq ($(origin VERSION),environment)
VERSION :=
endif
ifeq (version,$(firstword $(MAKECMDGOALS)))
ifneq ($(word 2,$(MAKECMDGOALS)),)
VERSION := $(word 2,$(MAKECMDGOALS))
$(eval $(VERSION):;@:)
endif
endif

.PHONY: check-endpoints release-prepare release-publish
.PHONY: help setup all lint fmt check test test-earthkit test-live test-all evals docs clean-cache \
        docs-check references references-check links dist version version-check clean

help: ## Show this help
	@if [ -t 1 ]; then c='\033[36m'; r='\033[0m'; else c=''; r=''; fi; \
	grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk -v c="$$c" -v r="$$r" 'BEGIN {FS = ":.*?## "}; {printf "  %s%-18s%s %s\n", c, $$1, r, $$2}'

setup: ## Create the dev environment (uv) and enable the pre-push hook
	$(UV) sync
	git config core.hooksPath .githooks

# ── Gate ─────────────────────────────────────────────────────────────────────

all: lint check test ## The gate before every commit: lint + check + offline tests

lint: ## Skill best-practice lint, ruff, formatting, REUSE licence headers
	$(PY) scripts/check_skills.py
	$(RUFF) check .
	$(RUFF) format --check .
	$(REUSE) lint

fmt: ## Apply ruff formatting, safe fixes, and refresh SKILL.md Contents line numbers
	$(PY) scripts/check_skills.py --fix
	$(RUFF) format .
	$(RUFF) check --fix .

check: ## Consistency: manifests/versions, generated docs, plugin manifest (if claude CLI present)
	$(PY) scripts/validate_packaging.py
	$(PY) scripts/gen_docs.py --check
	@if command -v claude >/dev/null 2>&1; then claude plugin validate . ; \
	 else echo "claude CLI not found — skipping 'claude plugin validate'"; fi

# ── Tests ────────────────────────────────────────────────────────────────────

test: ## Offline unit tests (default gate; no network, no earthkit)
	$(PYTEST) -q

test-earthkit: ## Offline tests that decode GRIB with earthkit (~250 MB first install)
	$(UV) run --group earthkit pytest -q -m "earthkit and not live"

test-live: ## Live tests against ECMWF services (network; some need credentials)
	$(UV) run --group earthkit pytest -q -m live

test-all: test test-earthkit test-live ## Every test layer except agent evals

evals: ## Fresh-agent skill evals: AGENT=claude|codex|gemini MODEL=… CASES="id1 id2" REPEAT=1
	$(PY) scripts/run_evals.py --agent $(or $(AGENT),claude) \
		$(if $(MODEL),--model $(MODEL)) --repeat $(or $(REPEAT),1) \
		$(foreach c,$(CASES),--case $(c))

# ── Generated artefacts ──────────────────────────────────────────────────────

docs: ## Regenerate docs/skills.md from every SKILL.md
	$(PY) scripts/gen_docs.py

references: ## Regenerate references from live ECMWF catalogues (network)
	$(PY) scripts/regenerate_references.py

references-check: ## Fail if generated references drifted from live catalogues (network)
	$(PY) scripts/regenerate_references.py --check

check-endpoints: ## (network) ECMWF status feeds, components and service hosts still exist
	$(PY) scripts/check_endpoints.py

links: ## Probe every URL in the skills (network)
	$(PY) scripts/check_links.py

dist: ## Build upload archives in dist/ (Claude and OpenAI)
	$(PY) scripts/build_dist.py

# ── Versioning ───────────────────────────────────────────────────────────────

version: ## Print the version, or set it everywhere: make version X.Y.Z
ifeq ($(VERSION),)
	@$(PY) scripts/bump_version.py --check
else
	@[[ "$(VERSION)" =~ ^[0-9]+\.[0-9]+\.[0-9]+$$ ]] || { \
		echo "error: version must be MAJOR.MINOR.MICRO without a 'v' prefix, got '$(VERSION)'" >&2; \
		exit 2; }
	$(PY) scripts/bump_version.py --set $(VERSION)
	$(PY) scripts/validate_packaging.py
	@echo "Now move the [Unreleased] entries in CHANGELOG.md under '## [$(VERSION)] - <date>'."
endif

version-check: ## Verify every manifest and SKILL.md agree on the version
	$(PY) scripts/validate_packaging.py

# ── Releasing (AGENTS.md "Releasing") ────────────────────────────────────────

release-prepare: ## Gate, bump, changelog, release PR: make release-prepare VERSION=X.Y.Z
	@test -n "$(VERSION)" || { echo "usage: make release-prepare VERSION=X.Y.Z" >&2; exit 2; }
	$(PY) scripts/release.py preflight $(VERSION) $(if $(ALLOW_MAJOR),--allow-major)
	$(MAKE) all test-earthkit test-live check-endpoints references-check
	git switch -c release/$(VERSION)
	$(PY) scripts/bump_version.py --set $(VERSION)
	$(PY) scripts/release.py changelog $(VERSION)
	$(MAKE) docs all
	git add -A
	git commit -m "chore: release $(VERSION)"
	git push -u origin release/$(VERSION)
	$(PY) scripts/release.py notes $(VERSION) | gh pr create --base main \
		--head release/$(VERSION) --title "chore: release $(VERSION)" --body-file -

release-publish: ## After the release PR is merged: tag X.Y.Z and publish the GitHub release
	@test -n "$(VERSION)" || { echo "usage: make release-publish VERSION=X.Y.Z" >&2; exit 2; }
	git switch main
	git pull --ff-only
	$(PY) scripts/release.py publish-check $(VERSION)
	git tag -a $(VERSION) -m "$(VERSION)"
	git push origin $(VERSION)
	$(PY) scripts/release.py notes $(VERSION) | gh release create $(VERSION) --title "$(VERSION)" \
		--notes-file -
	git branch -D release/$(VERSION) 2>/dev/null || true

# ── Cleanup ──────────────────────────────────────────────────────────────────

clean-cache: ## Remove the download and MIR caches (.cache/)
	rm -rf $(CACHE)

clean: ## Remove build outputs, caches and eval transcripts (never sources)
	rm -rf dist evals/runs .pytest_cache .ruff_cache
	find . -path ./.venv -prune -o -type d -name __pycache__ -print0 | xargs -0 rm -rf
	find . -path ./.venv -prune -o -type d -name .cache -path '*webmap*' -print0 | xargs -0 rm -rf
