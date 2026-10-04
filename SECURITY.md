<!--
SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
SPDX-License-Identifier: Apache-2.0
-->

# Security Policy

## Reporting a vulnerability

**Do not open a public GitHub issue.** Report privately, preferably through GitHub:
[Report a vulnerability](https://github.com/ecmwf/ecmwf-weather-skills/security/advisories/new)
(Security tab → "Report a vulnerability"). If you can't use GitHub, use the
[ECMWF Support Portal](https://support.ecmwf.int) with the subject "ECMWF Weather Skill Security
Vulnerability". We aim to acknowledge reports within 5 business days.

## Supported versions

Security fixes go into the latest release only.

## Scope

The skills, their helper scripts and the repository tooling. Of particular interest:

- leaking credentials (`~/.ecmwfapirc`, `~/.cdsapirc`, `~/.adsapirc`, `~/.polytopeapirc`, API
  keys in URLs) through script output, logs, generated web pages or eval transcripts;
- the `webmap.py serve` HTTP server (path traversal, request parameters);
- skill text that could steer an agent into unsafe actions.
- text from remote services (banners, errors, descriptions) that could act as instructions to an
  agent (prompt injection).

Vulnerabilities in ECMWF services or in third-party libraries (earthkit, eccodes, cdsapi,
polytope-client) should be reported to their respective projects.
