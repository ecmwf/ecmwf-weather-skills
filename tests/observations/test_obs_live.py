# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""obs.py against the real services: NOAA station metadata and METARs, WMO OSCAR, and tiny
MARS retrievals (one station, one day) that need an ECMWF key with observation rights.

  uv run --group earthkit pytest -m live tests/observations
"""

import json
import subprocess
import sys
from datetime import UTC, datetime, timedelta

import pytest
from conftest import SKILLS, load_script

obs = load_script("ecmwf-observations", "obs")
SCRIPT = SKILLS / "ecmwf-observations" / "scripts" / "obs.py"
needs_mars = pytest.mark.skipif(
    obs.credentials() is None, reason="no ECMWF Web API key (~/.ecmwfapirc) on this machine"
)
MARS_TIMEOUT_S = 900  # Web API queue plus the server-side scan of a day of reports


@pytest.mark.live
def test_live_icao_resolves_through_noaa():
    st = obs.resolve_station("LPPT")
    assert st["wmo"] == "08536" and st["icao"] == "LPPT"
    assert obs.NOAA_LABEL in st["source"]


@pytest.mark.live
def test_live_oscar_fallback_finds_a_wmo_station():
    st = obs.oscar_station("08535")
    assert st and "LISBOA" in st["name"] and abs(st["lat"] - 38.72) < 0.05


@pytest.mark.live
def test_live_latest_metar_is_labelled():
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "series", "--station", "EGLL", "--latest-metar", "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["source"] == obs.NOAA_LABEL and "NOT from ECMWF" in r["attribution"]
    assert r["rows"] and all(row["source"] == obs.NOAA_LABEL for row in r["rows"])


def _uv(*args):
    return subprocess.run(
        ["uv", "run", "--quiet", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=MARS_TIMEOUT_S,
    )


@pytest.mark.live
@needs_mars
def test_live_feedback_series_for_lisbon():
    day = (datetime.now(UTC) - timedelta(days=2)).date().isoformat()
    args = ("series", "--station", "08535", "--start", day, "--end", day, "--vars", "t2m,td")
    out = _uv(*args, "--json")
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout)
    t2m = [x for x in r["rows"] if x["variable"] == "t2m"]
    assert len(t2m) >= 12 and all(-40 < x["value"] < 50 for x in t2m)
    assert all(x["time_utc"].startswith(day) for x in r["rows"])
    assert any(x["fg_depar"] is not None for x in t2m)


@pytest.mark.live
@needs_mars
def test_live_bufr_series_for_lisbon():
    day = (datetime.now(UTC) - timedelta(days=4)).date().isoformat()
    args = ("series", "--station", "08535", "--start", day, "--end", day, "--raw")
    out = _uv(*args, "--vars", "t2m,precip", "--json")
    assert out.returncode == 0, out.stderr[-2000:]
    r = json.loads(out.stdout)
    assert r["dataset"] == "BUFR"
    # shape, not counts: some stations' hourly BUFR reports omit the temperature
    assert [x for x in r["rows"] if x["variable"] == "t2m"]
    assert {x["period_h"] for x in r["rows"] if x["variable"] == "precip"} - {None}
