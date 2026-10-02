---
name: mars
description: Writes and runs ECMWF MARS archive requests for licensed and member-state users, via the ECMWF Web API (ecmwf-api-client) or the mars client on ECMWF systems. Use when a task mentions MARS, the full IFS archive, class/stream/type/expver/levtype keywords, retrieving past operational forecasts or ensemble members beyond what Open Data publishes, ~/.ecmwfapirc, tape versus disk retrieval cost, or optimising large archive requests. Falls back to Open Data or CDS when the user has no MARS access and explains what access would add.
compatibility: Skill instructions are provider-neutral. Scripts need Python 3; request scripts use uv (PEP 723 inline dependencies, ecmwf-api-client) and network access to api.ecmwf.int.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.0"
---

# ECMWF MARS archive

## Contents
- Status — not implemented yet (see the repository PLAN.md)

This skill is a placeholder. Use the `open-data` skill for forecasts in the meantime.
