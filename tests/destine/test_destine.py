# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
import json
import subprocess
import sys

import pytest
from conftest import SKILLS, load_script

dt = load_script("destine", "destine")
SCRIPT = SKILLS / "destine" / "scripts" / "destine.py"


def cli(*args, home):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
        timeout=60,
    )


# --- credentials: a separate file, never ~/.polytopeapirc --------------------------------------


def test_token_location(tmp_path):
    assert dt.token_source(env={}, home=tmp_path) is None
    (tmp_path / ".polytopeapirc").write_text('{"user_key": "ecmwf"}')
    assert dt.token_source(env={}, home=tmp_path) is None  # ECMWF key, not DestinE
    (tmp_path / ".polytopeapirc-destine").write_text('{"user_key": "t"}')
    assert dt.token_source(env={}, home=tmp_path) == "~/.polytopeapirc-destine"
    assert (
        dt.token_source(env={"DESTINE_POLYTOPE_KEY": "x"}, home=tmp_path) == "DESTINE_POLYTOPE_KEY"
    )


def test_read_token_never_returned_by_check(tmp_path):
    (tmp_path / ".polytopeapirc-destine").write_text('{"user_key": "s3cr3t"}')
    assert dt.read_token(env={}, home=tmp_path) == "s3cr3t"
    out = cli("check", "--json", home=tmp_path)
    assert out.returncode == 0 and "s3cr3t" not in out.stdout


# --- setup instructions --------------------------------------------------------------------------


def test_setup_steps_cover_request_and_authentication():
    text = "\n".join(dt.setup_steps())
    for must in (
        "https://platform.destine.eu",
        "https://platform.destine.eu/access-policy-upgrade/",
        "upgraded access",
        "desp-authentication.py",
        "-o ~/.polytopeapirc-destine",
        "https://platform.destine.eu/contact/",
    ):
        assert must in text, must
    assert "-p " not in text  # never suggest a password on the command line


def test_check_without_token_offers_setup(tmp_path):
    out = cli("check", "--json", home=tmp_path)
    assert out.returncode == 4
    d = json.loads(out.stdout)
    assert d["token"] is None and "destine.py setup" in d["offer"]


# --- addresses and templates ---------------------------------------------------------------


@pytest.mark.parametrize(
    "dataset, model, expected",
    [
        ("climate-dt", "ifs-fesom", "polytope.lumi.apps.dte.destination-earth.eu"),
        ("climate-dt", "icon", "polytope.lumi.apps.dte.destination-earth.eu"),
        ("climate-dt", "ifs-nemo", "polytope.mn5.apps.dte.destination-earth.eu"),
        ("extremes-dt", None, "polytope.lumi.apps.dte.destination-earth.eu"),
        ("on-demand-extremes-dt", None, "polytope.lumi.apps.dte.destination-earth.eu"),
    ],
)
def test_address(dataset, model, expected):
    req = {"dataset": dataset, **({"model": model} if model else {})}
    assert dt.address_for(req) == expected


def test_storylines_are_on_mn5():
    req = {"dataset": "climate-dt", "activity": "story-nudging", "model": "ifs-fesom"}
    assert dt.address_for(req) == "polytope.mn5.apps.dte.destination-earth.eu"


def test_climate_template():
    r = dt.template("climate-dt", param="167", start="2014-01-01", end="2014-01-31")
    assert r["class"] == "d1" and r["dataset"] == "climate-dt" and r["generation"] == "2"
    assert r["date"] == "20140101/to/20140131" and r["stream"] == "clte"
    assert dt.address_for(r).startswith("polytope.lumi")


def test_extremes_template_and_point_feature():
    r = dt.template("extremes-dt", param="167", lat=38.72, lon=-9.14)
    assert r["dataset"] == "extremes-dt" and r["stream"] == "oper" and r["date"] == "-1"
    assert r["feature"]["type"] == "timeseries" and r["feature"]["points"] == [[38.72, -9.14]]
    assert r["feature"]["time_axis"] == "step" and "format" not in r


def test_lint_catches_common_mistakes():
    errs = "\n".join(
        dt.lint({"class": "od", "dataset": "climate-dt", "generation": "2", "format": "covjson"})
    )
    assert "class=d1" in errs and "format" in errs and "model" in errs


def test_attribution_is_the_ec_text():
    a = dt.attribution("climate-dt")
    assert "European Commission" in a["text"] and "Destination Earth" in a["text"]
    assert "10.21957/79c6af3105" in a["citation"] and "redistribut" in a["note"]


def test_cli_request_prints_address_and_request(tmp_path):
    out = cli(
        "request",
        "climate-dt",
        "--param",
        "167",
        "--start",
        "2014-01-01",
        "--end",
        "2014-01-31",
        "--model",
        "ifs-nemo",
        "--json",
        home=tmp_path,
    )
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["address"] == "polytope.mn5.apps.dte.destination-earth.eu"
    assert d["request"]["model"] == "ifs-nemo" and d["errors"] == []
