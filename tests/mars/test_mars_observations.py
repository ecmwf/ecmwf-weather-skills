# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""mars.py lint accepts observation requests (BUFR reports, ODB feedback) on their own terms."""

import json
import subprocess
import sys

import pytest
from conftest import SKILLS, load_script

mars = load_script("ecmwf-mars", "mars")
SCRIPT = SKILLS / "ecmwf-mars" / "scripts" / "mars.py"

FILTER = (
    '"select statid@hdr,date@hdr,time@hdr,varno@body,obsvalue@body,fg_depar@body '
    "where statid@hdr = '   08535' and varno@body in (39,40)\""
)
FEEDBACK_TEXT = f"""retrieve,
 class=od, type=ofb, stream=oper, expver=1,
 date=20261009, time=06, reportype=16076,
 format=odb,
 filter={FILTER}"""
BUFR = {
    "class": "od",
    "type": "ob",
    "stream": "oper",
    "expver": "1",
    "obsgroup": "conv",
    "obstype": "lsd",
    "date": "2026-10-01/to/2026-10-07",
    "time": "00/03/06/09/12/15/18/21",
    "range": "179",
    "ident": "08535",
}


def test_quoted_filter_with_commas_is_one_value():
    req = mars.parse_request(FEEDBACK_TEXT)
    assert req["filter"] == FILTER
    assert req["format"] == "odb" and req["reportype"] == "16076"
    assert f"filter={FILTER}" in mars.format_request(req)


def test_feedback_request_is_clean():
    r = mars.lint(mars.parse_request(FEEDBACK_TEXT))
    assert r["errors"] == [], r["errors"]
    assert not any("grid" in w for w in r["warnings"])  # observations are not gridded


def test_bufr_request_is_clean():
    r = mars.lint(BUFR)
    assert r["errors"] == [], r["errors"]


def test_observation_estimate_is_not_a_grib_size():
    e = mars.estimate(BUFR)
    assert e["fields"] == 0 and "observation" in e["note"]


def test_feedback_needs_odb_format_and_a_report_type():
    req = mars.parse_request(FEEDBACK_TEXT)
    req.pop("reportype")
    req["format"] = "grib"
    errs = "\n".join(mars.lint(req)["errors"])
    assert "format=odb" in errs and "reportype" in errs


def test_filter_columns_need_their_table():
    req = {**mars.parse_request(FEEDBACK_TEXT), "filter": '"select statid,obsvalue@body"'}
    assert any("@hdr" in e or "@body" in e for e in mars.lint(req)["errors"])


def test_unquoted_filter_is_an_error():
    req = {**mars.parse_request(FEEDBACK_TEXT), "filter": "select obsvalue@body"}
    assert any("quote" in e for e in mars.lint(req)["errors"])


def test_bufr_needs_obsgroup_or_obstype_and_numeric_ident():
    bad = {k: v for k, v in BUFR.items() if k not in ("obsgroup", "obstype")}
    bad["ident"] = "LPPT"
    errs = "\n".join(mars.lint(bad)["errors"])
    assert "obstype" in errs and "ident" in errs and "area" in errs


def test_observation_requests_still_check_shared_rules():
    errs = "\n".join(mars.lint({**BUFR, "area": "38/-10/40/-8", "levelist": "1"})["errors"])
    assert "north" in errs and "levelist" in errs


def test_format_stays_wrong_for_fields():
    hres = {
        "class": "od",
        "stream": "oper",
        "type": "fc",
        "levtype": "sfc",
        "param": "2t",
        "date": "-1",
        "time": "00",
        "step": "24",
        "format": "netcdf",
    }
    assert any("format" in e for e in mars.lint(hres)["errors"])


def test_cli_lint_accepts_a_feedback_request_file(tmp_path):
    p = tmp_path / "ofb.mars"
    p.write_text(FEEDBACK_TEXT)
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "lint", str(p), "--json"], capture_output=True, text=True
    )
    r = json.loads(out.stdout)
    assert out.returncode == 0, r["errors"]
    assert "licen" in r["licence"].lower()


@pytest.mark.earthkit
def test_pymetkit_accepts_observation_requests(tmp_path):
    # ECMWF's MARS language (metkit) knows the observation keywords and values
    metkit = ["uv", "run", "--no-project", "--with", "pymetkit>=1.19,<2", "python3"]
    bufr = mars.format_request(BUFR)
    for i, text in enumerate((FEEDBACK_TEXT, bufr)):
        p = tmp_path / f"r{i}.mars"
        p.write_text(text)
        out = subprocess.run(
            [*metkit, str(SCRIPT), "lint", str(p), "--json"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        r = json.loads(out.stdout)
        assert out.returncode == 0 and r["metkit"] == "checked", r["errors"]
