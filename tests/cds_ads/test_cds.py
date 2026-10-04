# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys

import pytest
from conftest import FIXTURES, SKILLS, load_script

cds = load_script("cds-ads", "cds")
FORM_TS = json.loads((FIXTURES / "cds-form-era5-ts.json").read_text())
FORM_SL = json.loads((FIXTURES / "cds-form-era5-sl.json").read_text())
COLL_TS = json.loads((FIXTURES / "cds-collection-era5-ts.json").read_text())
SEARCH = json.loads((FIXTURES / "cds-search-era5.json").read_text())
SCRIPT = SKILLS / "cds-ads" / "scripts" / "cds.py"


# --- credentials (names only) -------------------------------------------------------------------


def test_credentials_cds(tmp_path):
    assert cds.credentials("cds", env={}, home=tmp_path) is None
    assert (
        cds.credentials("cds", env={"CDSAPI_KEY": "x", "CDSAPI_URL": "u"}, home=tmp_path)
        == "CDSAPI_URL/CDSAPI_KEY"
    )
    (tmp_path / ".cdsapirc").write_text("url: https://cds.climate.copernicus.eu/api\nkey: abc\n")
    assert cds.credentials("cds", env={}, home=tmp_path) == "~/.cdsapirc"


def test_credentials_ads_only_file(tmp_path):
    assert cds.credentials("ads", env={"CDSAPI_KEY": "x"}, home=tmp_path) is None
    (tmp_path / ".adsapirc").write_text("url: x\nkey: y\n")
    assert cds.credentials("ads", env={}, home=tmp_path) == "~/.adsapirc"


def test_cdsapirc_pointing_at_ads_is_not_cds(tmp_path):
    (tmp_path / ".cdsapirc").write_text("url: https://ads.atmosphere.copernicus.eu/api\nkey: k\n")
    assert cds.credentials("cds", env={}, home=tmp_path) is None


# --- catalogue ------------------------------------------------------------------------------------


def test_search_results():
    hits = cds.search_results(SEARCH, "era5-land")
    ids = [h["id"] for h in hits]
    assert "reanalysis-era5-land" in ids and all("land" in i for i in ids)


def test_form_options():
    o = cds.form_options(FORM_SL)
    assert "2m_temperature" in o["variable"]["values"]
    assert "reanalysis" in o["product_type"]["values"]
    assert o["month"]["values"][0] == "01"
    ots = cds.form_options(FORM_TS)
    assert ots["date"]["range"][0] == "1940-01-01"
    assert set(ots["data_format"]["values"]) == {"netcdf", "csv"}


def test_describe_licence_doi_attribution():
    d = cds.describe(COLL_TS, FORM_TS, store="cds")
    assert d["licence"] == "CC-BY-4.0" and d["doi"] == "10.24381/1cf1ad76"
    assert "Copernicus Climate Change Service" in d["attribution"]
    assert "Neither the European Commission nor ECMWF" in d["attribution"]
    assert "10.24381/1cf1ad76" in d["citation"]


def test_attribution_ads_mentions_cams():
    assert "Copernicus Atmosphere Monitoring Service" in cds.attribution("ads", 2026)


# --- validation -----------------------------------------------------------------------------------


def test_validate_ok_timeseries():
    req = {
        "variable": ["2m_temperature"],
        "location": {"latitude": 38.72, "longitude": -9.14},
        "date": ["1991-01-01/2020-12-31"],
        "data_format": "csv",
    }
    assert cds.validate(req, FORM_TS) == []


def test_validate_catches_bad_values_with_suggestions():
    req = {
        "variable": ["2m_temp"],
        "date": ["1930-01-01/1931-01-01"],
        "data_format": "xlsx",
        "location": {"latitude": 95, "longitude": 0},
        "bogus": 1,
    }
    errs = "\n".join(cds.validate(req, FORM_TS))
    assert "2m_temp" in errs and "2m_temperature" in errs  # close-match suggestion
    assert "1940-01-01" in errs and "xlsx" in errs and "latitude" in errs and "bogus" in errs


def test_validate_single_levels_lists():
    req = {
        "product_type": ["reanalysis"],
        "variable": ["total_precipitation"],
        "year": ["2020"],
        "month": ["13"],
        "day": ["01"],
        "time": ["00:00"],
        "data_format": "netcdf",
        "area": [42.2, -9.6, 36.9, -6.1],
    }
    errs = cds.validate(req, FORM_SL)
    assert len(errs) == 1 and "13" in errs[0]


def test_era5_point_request():
    r = cds.era5_point_request(38.72, -9.14, "1991-01-01", "2020-12-31", ["2m_temperature"], "csv")
    assert r == {
        "variable": ["2m_temperature"],
        "location": {"latitude": 38.72, "longitude": -9.14},
        "date": ["1991-01-01/2020-12-31"],
        "data_format": "csv",
    }
    assert cds.validate(r, FORM_TS) == []


# --- CLI ------------------------------------------------------------------------------------------


def test_cli_check_without_credentials(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "check", "--json"],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 4
    d = json.loads(out.stdout)
    assert d["cds"] is None and "cds.climate.copernicus.eu" in d["how_to_get_a_key"]


def test_cli_retrieve_without_credentials_never_prompts(tmp_path):
    req = tmp_path / "r.json"
    req.write_text(json.dumps({"variable": ["2m_temperature"]}))
    out = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "retrieve",
            "--dataset",
            "reanalysis-era5-single-levels-timeseries",
            "--request",
            str(req),
            "-o",
            str(tmp_path / "x.nc"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        stdin=subprocess.DEVNULL,
    )
    assert out.returncode == 4 and "cdsapirc" in out.stderr


@pytest.mark.live
def test_live_validate_with_costing(tmp_path):
    out = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "era5-point",
            "--lat",
            "38.72",
            "--lon",
            "-9.14",
            "--start",
            "1991-01-01",
            "--end",
            "2020-12-31",
            "--variable",
            "2m_temperature",
            "--dry-run",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    d = json.loads(out.stdout)
    assert d["valid"] and d["cost"]["cost"] <= d["cost"]["limit"]


def test_ads_key_in_cdsapirc_is_found_but_flagged(tmp_path):
    (tmp_path / ".cdsapirc").write_text("url: https://ads.atmosphere.copernicus.eu/api\nkey: k\n")
    src = cds.credentials("ads", env={}, home=tmp_path)
    assert src.startswith("~/.cdsapirc") and "~/.adsapirc" in src


def test_setup_steps_cds_and_ads():
    c = "\n".join(cds.setup_steps("cds"))
    for must in (
        "Register new user",
        "https://cds.climate.copernicus.eu/how-to-api",
        "url: https://cds.climate.copernicus.eu/api",
        "chmod 600 ~/.cdsapirc",
        "Terms of use",
    ):
        assert must in c, must
    a = "\n".join(cds.setup_steps("ads"))
    assert "url: https://ads.atmosphere.copernicus.eu/api" in a and "~/.adsapirc" in a


def test_check_without_key_offers_setup(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "check", "--json"],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    d = json.loads(out.stdout)
    assert "cds.py setup" in d["offer"]


def test_cli_setup(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "setup", "--store", "ads"], capture_output=True, text=True
    )
    assert out.returncode == 0 and "ads.atmosphere.copernicus.eu/how-to-api" in out.stdout


# --- dataset licences ------------------------------------------------------------------------


def test_required_licences_from_form():
    lic = cds.required_licences(FORM_SL)
    assert lic == [{"id": "cc-by", "revision": 1, "label": "CC-BY licence"}]


def test_licence_status_and_accept_url():
    accepted = {"licences": [{"id": "terms-of-use-cds", "revision": 11}]}
    st = cds.licence_status("cds", "reanalysis-era5-single-levels", FORM_SL, accepted)
    assert st["missing"] == ["CC-BY licence"]
    assert st["accept_at"] == (
        "https://cds.climate.copernicus.eu/datasets/"
        "reanalysis-era5-single-levels?tab=download#manage-licences"
    )
    ok = {"licences": [{"id": "cc-by", "revision": 1}]}
    assert cds.licence_status("cds", "x", FORM_SL, ok)["missing"] == []


def test_accepted_licences_sends_token_header_only(tmp_path):
    seen = {}

    def fetch(url, headers):
        seen.update(url=url, headers=headers)
        return {"licences": []}

    (tmp_path / ".cdsapirc").write_text(
        "url: https://cds.climate.copernicus.eu/api\nkey: abc-123\n"
    )
    cds.accepted_licences("cds", env={}, home=tmp_path, fetch=fetch)
    assert seen["url"] == "https://cds.climate.copernicus.eu/api/profiles/v1/account/licences"
    assert seen["headers"] == {"PRIVATE-TOKEN": "abc-123"}
    assert cds.accepted_licences("cds", env={}, home=tmp_path / "none", fetch=fetch) is None


def test_setup_names_the_licence_page():
    s = "\n".join(cds.setup_steps("cds"))
    assert "?tab=download#manage-licences" in s and "CC-BY licence" in s


# --- barriers: detailed instructions whenever access is blocked -------------------------------


def test_licence_steps_are_detailed():
    s = "\n".join(cds.licence_steps("ecds", "tigge-forecasts", ["TIGGE licence"]))
    for must in (
        "https://ecds.ecmwf.int",
        "Login",
        "tigge-forecasts?tab=download#manage-licences",
        "Terms of use",
        "Accept",
        "TIGGE licence",
        "profile?tab=licences",
        "re-run",
    ):
        assert must in s, must


def test_barrier_for_errors():
    lic = cds.barrier_for_error(
        "cds", "reanalysis-era5-single-levels", "403 Client Error: required licences not accepted"
    )
    assert "licence" in lic["blocked"].lower() and lic["user_steps"]
    assert "until the user confirms" in lic["agent"]
    key = cds.barrier_for_error("ads", "x", "401 Unauthorized: invalid token")
    assert "how-to-api" in "\n".join(key["user_steps"])
    assert cds.barrier_for_error("cds", "x", "queue is full") is None


def test_licence_barrier_from_validation():
    res = {"licences": {"missing": ["CC-BY licence"], "accept_at": "u"}}
    b = cds.licence_barrier("cds", "reanalysis-era5-single-levels", res)
    assert "CC-BY licence" in "\n".join(b["user_steps"])
    assert cds.licence_barrier("cds", "d", {"licences": {"missing": []}}) is None


def test_ecds_store_known():
    assert cds.STORES["ecds"]["api"] == "https://ecds.ecmwf.int/api"


def test_cli_retrieve_without_key_is_blocked_with_steps(tmp_path):
    req = tmp_path / "r.json"
    req.write_text("{}")
    out = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "retrieve",
            "--dataset",
            "x",
            "--request",
            str(req),
            "-o",
            str(tmp_path / "o"),
        ],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 4
    assert "BLOCKED:" in out.stderr and "how-to-api" in out.stderr and "agent" in out.stderr.lower()


def test_ecds_uses_the_same_token_as_cds(tmp_path):
    (tmp_path / ".cdsapirc").write_text("url: https://cds.climate.copernicus.eu/api\nkey: t\n")
    assert cds.credentials("ecds", env={}, home=tmp_path).startswith("~/.cdsapirc")
    s = "\n".join(cds.setup_steps("ecds"))
    assert "https://ecds.ecmwf.int/how-to-api" in s and "same personal access token" in s


def test_cli_check_lists_ecds(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "check", "--json"],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert "ecds" in json.loads(out.stdout)


def test_attribution_ecds_is_not_copernicus():
    a = cds.attribution("ecds", 2026)
    assert "Copernicus" not in a and "ECMWF" in a


def test_server_rejection_makes_request_invalid():
    res = {"valid": True, "errors": []}
    cds.apply_costing(
        res,
        {
            "cost": 1.0,
            "limit": 10.0,
            "request_is_valid": False,
            "invalid_reason": "request: {} should be non-empty",
        },
    )
    assert res["valid"] is False and "non-empty" in res["errors"][0]
