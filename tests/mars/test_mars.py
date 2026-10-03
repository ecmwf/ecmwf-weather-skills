# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys

from conftest import FIXTURES, SKILLS, load_script

mars = load_script("mars", "mars")
SCRIPT = SKILLS / "mars" / "scripts" / "mars.py"

HRES = {
    "class": "od",
    "stream": "oper",
    "type": "fc",
    "expver": "1",
    "levtype": "sfc",
    "param": "2t/10u/10v",
    "date": "2024-03-01/to/2024-03-31",
    "time": "00",
    "step": "0/to/72/by/6",
    "grid": "0.25/0.25",
    "area": "72/-25/30/45",
}


# --- parsing / formatting -------------------------------------------------------------------------


def test_parse_mars_text_and_back():
    req = mars.parse_request(
        "retrieve,\n class=od, stream=oper,type=fc,\n param=167.128/165.128, date=-1"
    )
    assert req == {
        "class": "od",
        "stream": "oper",
        "type": "fc",
        "param": "167.128/165.128",
        "date": "-1",
    }
    text = mars.format_request(req)
    assert text.startswith("retrieve,") and "param=167.128/165.128" in text


def test_expand_values():
    assert mars.expand("2024-03-01/to/2024-03-03") == [
        "2024-03-01",
        "2024-03-02",
        "2024-03-03",
    ]
    assert mars.expand("20240301/to/20240310/by/3") == [
        "20240301",
        "20240304",
        "20240307",
        "20240310",
    ]
    assert mars.expand("0/to/72/by/6") == [str(s) for s in range(0, 73, 6)]
    assert mars.expand("1000/850/500") == ["1000", "850", "500"]
    assert mars.expand("00/12") == ["00", "12"]


# --- lint -----------------------------------------------------------------------------------------


def test_lint_clean_request():
    assert mars.lint(HRES)["errors"] == []


def test_lint_errors():
    bad = {**HRES, "levtype": "pl"}  # pl needs levelist
    bad.pop("class")
    r = mars.lint(bad)
    errs = "\n".join(r["errors"])
    assert "class" in errs and "levelist" in errs


def test_lint_area_and_grid():
    r = mars.lint({**HRES, "area": "30/-25/72/45", "grid": "0.25"})
    errs = "\n".join(r["errors"])
    assert "north" in errs.lower() and "grid" in errs.lower()


def test_lint_pf_needs_number_and_an_has_no_step():
    assert any("number" in e for e in mars.lint({**HRES, "stream": "enfo", "type": "pf"})["errors"])
    assert any("step" in w for w in mars.lint({**HRES, "type": "an"})["warnings"])


def test_lint_warns_multi_month_archive_requests():
    era5 = {
        "class": "ea",
        "stream": "oper",
        "type": "an",
        "expver": "1",
        "levtype": "pl",
        "levelist": "all",
        "param": "t",
        "date": "2010-01-01/to/2010-12-31",
        "time": "00/to/23/by/1",
    }
    w = "\n".join(mars.lint(era5)["warnings"])
    assert "month" in w.lower()


# --- estimate / plan ------------------------------------------------------------------------------


def test_estimate_fields_and_size():
    e = mars.estimate(HRES)
    assert e["fields"] == 3 * 31 * 1 * 13
    # 0.25° Europe box: (70/0.25+1) x (42/0.25+1) points, ~2 bytes each in GRIB
    assert 50e6 < e["bytes"] < 200e6


def test_estimate_levelist_and_members():
    e = mars.estimate(
        {
            "class": "od",
            "stream": "enfo",
            "type": "pf",
            "levtype": "pl",
            "levelist": "1000/850/500",
            "param": "t",
            "date": "-1",
            "time": "00",
            "step": "24",
            "number": "1/to/50",
            "grid": "1/1",
        }
    )
    assert e["fields"] == 3 * 50


def test_plan_splits_by_month():
    era5 = {
        "class": "ea",
        "stream": "oper",
        "type": "an",
        "expver": "1",
        "levtype": "pl",
        "levelist": "all",
        "param": "t",
        "date": "2010-01-01/to/2010-03-31",
        "time": "00/to/23/by/1",
    }
    chunks = mars.plan(era5)
    assert [c["date"] for c in chunks] == [
        "2010-01-01/to/2010-01-31",
        "2010-02-01/to/2010-02-28",
        "2010-03-01/to/2010-03-31",
    ]
    assert all(c["param"] == "t" and c["levelist"] == "all" for c in chunks)


def test_parse_cost_output():
    c = mars.parse_cost((FIXTURES / "mars-list-cost.txt").read_text())
    assert c == {
        "size": 26409176,
        "number_of_fields": 2,
        "online_size": 26409176,
        "off_line_size": 0,
        "number_of_tape_files": 0,
        "number_of_disk_files": 2,
        "number_of_online_fields": 2,
        "number_of_offline_fields": 0,
        "number_of_tapes": 0,
    }


# --- credentials / CLI ----------------------------------------------------------------------------


def test_credentials(tmp_path):
    assert mars.credentials(env={}, home=tmp_path, which=lambda c: None) == {
        "webapi": None,
        "mars_client": None,
    }
    (tmp_path / ".ecmwfapirc").write_text("{}")
    c = mars.credentials(env={}, home=tmp_path, which=lambda c: "/usr/local/bin/mars")
    assert c == {"webapi": "~/.ecmwfapirc", "mars_client": "/usr/local/bin/mars"}
    assert (
        mars.credentials(
            env={"ECMWF_API_KEY": "k", "ECMWF_API_URL": "u", "ECMWF_API_EMAIL": "e"},
            home=tmp_path,
            which=lambda c: None,
        )["webapi"]
        == "ECMWF_API_* environment"
    )


def test_licence_note():
    assert "CC BY" in mars.licence_note({"class": "ea"})
    assert "licence" in mars.licence_note({"class": "od"}).lower()


def test_cli_lint_json(tmp_path):
    f = tmp_path / "r.json"
    f.write_text(json.dumps(HRES))
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "lint", str(f), "--json"],
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["errors"] == [] and d["estimate"]["fields"] == 1209 and "retrieve," in d["mars"]


def test_cli_check_without_credentials(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "check", "--json"],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 4 and json.loads(out.stdout)["webapi"] is None
