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
        cds.credentials(
            "cds", env={"CDSAPI_KEY": "x", "CDSAPI_URL": "u"}, home=tmp_path
        )
        == "CDSAPI_URL/CDSAPI_KEY"
    )
    (tmp_path / ".cdsapirc").write_text(
        "url: https://cds.climate.copernicus.eu/api\nkey: abc\n"
    )
    assert cds.credentials("cds", env={}, home=tmp_path) == "~/.cdsapirc"


def test_credentials_ads_only_file(tmp_path):
    assert cds.credentials("ads", env={"CDSAPI_KEY": "x"}, home=tmp_path) is None
    (tmp_path / ".adsapirc").write_text("url: x\nkey: y\n")
    assert cds.credentials("ads", env={}, home=tmp_path) == "~/.adsapirc"


def test_cdsapirc_pointing_at_ads_is_not_cds(tmp_path):
    (tmp_path / ".cdsapirc").write_text(
        "url: https://ads.atmosphere.copernicus.eu/api\nkey: k\n"
    )
    assert cds.credentials("cds", env={}, home=tmp_path) is None


# --- catalogue -------------------------------------------------------------------------------------


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


# --- validation ------------------------------------------------------------------------------------


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
    assert (
        "1940-01-01" in errs
        and "xlsx" in errs
        and "latitude" in errs
        and "bogus" in errs
    )


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
    r = cds.era5_point_request(
        38.72, -9.14, "1991-01-01", "2020-12-31", ["2m_temperature"], "csv"
    )
    assert r == {
        "variable": ["2m_temperature"],
        "location": {"latitude": 38.72, "longitude": -9.14},
        "date": ["1991-01-01/2020-12-31"],
        "data_format": "csv",
    }
    assert cds.validate(r, FORM_TS) == []


# --- CLI -------------------------------------------------------------------------------------------


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
