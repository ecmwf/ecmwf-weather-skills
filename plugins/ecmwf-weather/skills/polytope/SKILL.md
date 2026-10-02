---
name: polytope
description: Extract just the data needed from ECMWF datacubes with Polytope feature extraction — point time series, vertical profiles, polygons, bounding boxes and trajectories — instead of downloading whole global fields. Use when a task asks for a forecast or meteogram at a specific location, weather along a route, data for a country or polygon, the Polytope client or earthkit polytope source, ~/.polytopeapirc, or how to answer 'what is the weather in X' efficiently. Includes the end-user point-forecast workflow with automatic fallback to Open Data plus local nearest-gridpoint extraction when Polytope credentials are missing.
compatibility: Skill instructions are provider-neutral. Scripts need Python 3; extraction scripts use uv (PEP 723 inline dependencies, polytope-client, earthkit-data) and network access to ECMWF Polytope endpoints.
license: Apache-2.0
metadata:
  author: ECMWF
  version: "0.1.0"
---

# ECMWF Polytope

> Status — scaffold. Content to be written per `PLAN.md`.
