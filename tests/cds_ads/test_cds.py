# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
import urllib.error

import pytest
from conftest import FIXTURES, SKILLS, load_script

cds = load_script("ecmwf-cds-ads", "cds")
FORM_TS = json.loads((FIXTURES / "cds-form-era5-ts.json").read_text())
FORM_SL = json.loads((FIXTURES / "cds-form-era5-sl.json").read_text())
COLL_TS = json.loads((FIXTURES / "cds-collection-era5-ts.json").read_text())
SEARCH = json.loads((FIXTURES / "cds-search-era5.json").read_text())
SCRIPT = SKILLS / "ecmwf-cds-ads" / "scripts" / "cds.py"


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


def test_form_link_must_be_https(monkeypatch):
    monkeypatch.setattr(cds, "_http", lambda url, body=None: [])
    with pytest.raises(ValueError, match="https"):
        cds.get_form({"links": [{"rel": "form", "href": "http://example.org/form.json"}]})
    assert (
        cds.get_form({"links": [{"rel": "form", "href": "https://cds.climate.copernicus.eu/f"}]})
        == []
    )


def test_unpack_zip_download(tmp_path):
    import zipfile

    src = tmp_path / "dl.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("reanalysis-era5-single-levels-timeseries-sfc_x.csv", "valid_time,t2m\n")
    out = tmp_path / "lisbon.csv"
    assert cds.unpack_download(src, out) == [out]
    assert out.read_text().startswith("valid_time,t2m")


def test_unpack_multi_member_zip_into_directory(tmp_path):
    import zipfile

    src = tmp_path / "dl.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.nc", "A")
        z.writestr("b.nc", "B")
    files = cds.unpack_download(src, tmp_path / "out.nc")
    assert sorted(f.name for f in files) == ["a.nc", "b.nc"] and files[0].parent.name == "out"


def test_unpack_plain_file_is_copied(tmp_path):
    src = tmp_path / "x.grib"
    src.write_bytes(b"GRIB")
    assert cds.unpack_download(src, tmp_path / "y.grib")[0].read_bytes() == b"GRIB"


def test_unpack_rejects_path_traversal(tmp_path):
    import zipfile

    src = tmp_path / "evil.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("../../etc/x", "A")
        z.writestr("ok.nc", "B")
    with pytest.raises(ValueError, match="unsafe"):
        cds.unpack_download(src, tmp_path / "o.nc")


# --- licence status is always explicit -------------------------------------------------------


def test_licence_states():
    ok = cds.licence_status("cds", "d", FORM_SL, {"licences": [{"id": "cc-by"}]})
    assert ok["state"] == "accepted" and ok["missing"] == []
    no = cds.licence_status("cds", "d", FORM_SL, {"licences": []})
    assert no["state"] == "missing" and no["missing"] == ["CC-BY licence"]
    unk = cds.licence_status("cds", "d", FORM_SL, None, reason="no key readable")
    assert unk["state"] == "unchecked" and unk["reason"] == "no key readable"


def test_licence_line_always_states_the_result():
    ok = cds.licence_line(cds.licence_status("cds", "d", FORM_SL, {"licences": [{"id": "cc-by"}]}))
    assert ok.startswith("licences: accepted") and "confirmed on your account" in ok
    no = cds.licence_line(cds.licence_status("cds", "d", FORM_SL, {"licences": []}))
    assert no.startswith("licences: NOT accepted") and "CC-BY licence" in no
    unk = cds.licence_line(cds.licence_status("cds", "d", FORM_SL, None, reason="HTTP 500"))
    assert (
        unk.startswith("licences: not checked") and "HTTP 500" in unk and "manage-licences" in unk
    )


@pytest.fixture
def offline_store(monkeypatch):
    monkeypatch.setattr(cds, "get_collection", lambda s, d: {"license": "CC-BY-4.0"})
    monkeypatch.setattr(cds, "get_form", lambda c: FORM_TS)
    monkeypatch.setattr(cds, "costing", lambda s, d, r: {"cost": 1.0, "limit": 10.0})
    return monkeypatch


REQ = {
    "variable": ["2m_temperature"],
    "location": {"latitude": 38.72, "longitude": -9.14},
    "date": ["1991-01-01/2020-12-31"],
    "data_format": "csv",
}


def test_validate_confirms_accepted_licence(offline_store):
    offline_store.setattr(cds, "accepted_licences", lambda s: {"licences": [{"id": "cc-by"}]})
    res = cds._validate_and_cost("cds", "reanalysis-era5-single-levels-timeseries", REQ)
    assert res["licences"]["state"] == "accepted"


def test_validate_reports_why_licence_was_not_checked(offline_store):
    offline_store.setattr(cds, "accepted_licences", lambda s: None)
    res = cds._validate_and_cost("cds", "x", REQ)
    assert res["licences"]["state"] == "unchecked" and "key" in res["licences"]["reason"]

    def boom(store):
        raise urllib.error.URLError("timed out")

    offline_store.setattr(cds, "accepted_licences", boom)
    res = cds._validate_and_cost("cds", "x", REQ)
    assert res["licences"]["state"] == "unchecked" and "timed out" in res["licences"]["reason"]


FORM_CERRA = json.loads((FIXTURES / "cds-form-cerra-sl.json").read_text())
CERRA_REQ = {
    "variable": ["2m_temperature"],
    "level_type": "surface_or_atmosphere",
    "data_type": ["reanalysis"],
    "product_type": "analysis",
    "year": ["2023"],
    "month": ["07"],
    "day": ["18"],
    "time": ["12:00"],
    "data_format": "grib",
}


def test_cerra_form_has_no_area_input():
    assert "area" not in cds.form_options(FORM_CERRA)


def test_grid_is_accepted_as_post_processing():
    assert cds.validate({**CERRA_REQ, "grid": [0.05, 0.05]}, FORM_CERRA) == []


def test_area_needs_grid_when_the_form_has_no_area():
    # CARRA/CERRA are on Lambert grids: MARS cannot crop them, the job fails server-side.
    errs = "\n".join(cds.validate({**CERRA_REQ, "area": [43, -10, 36, -6]}, FORM_CERRA))
    assert "grid" in errs and "Lambert" in errs
    ok = {**CERRA_REQ, "area": [43, -10, 36, -6], "grid": [0.05, 0.05]}
    assert cds.validate(ok, FORM_CERRA) == []


def test_post_processing_values_are_checked():
    bad = {**CERRA_REQ, "area": [36, -10, 43, -6], "grid": [0.05]}
    errs = "\n".join(cds.validate(bad, FORM_CERRA))
    assert "[N, W, S, E]" in errs and "grid must be" in errs


def test_era5_area_still_needs_no_grid():
    opts = cds.form_options(FORM_SL)
    assert "area" in opts


# --- check verifies the key; malformed rc files ------------------------------------------------

FAKE = "zz-fake-token-zz"
CDS_RC = f"url: https://cds.climate.copernicus.eu/api\nkey: {FAKE}\n"


def test_token_reads_json_rc_files(tmp_path):
    rc = {"url": "https://cds.climate.copernicus.eu/api", "key": FAKE}
    (tmp_path / ".cdsapirc").write_text(json.dumps(rc, indent=1))
    assert cds._token("cds", {}, tmp_path) == FAKE


def test_check_verifies_the_key(tmp_path, capsys):
    (tmp_path / ".cdsapirc").write_text(CDS_RC)
    seen = []

    def fetch(url, headers):
        seen.append((url, headers))
        return 200, {"licences": [{"id": "cc-by"}, {"id": "licence-to-use-copernicus-products"}]}

    assert cds.check(True, env={}, home=tmp_path, fetch=fetch) == 0
    raw = capsys.readouterr().out
    out = json.loads(raw)
    assert out["cds_key"] == {"verified": True, "accepted_licences": 2}
    assert seen == [
        (
            "https://cds.climate.copernicus.eu/api/profiles/v1/account/licences",
            {"PRIVATE-TOKEN": FAKE},
        )
    ]
    assert FAKE not in raw


def test_check_rejected_key_is_blocked(tmp_path, capsys):
    (tmp_path / ".cdsapirc").write_text(CDS_RC)
    assert cds.check(True, env={}, home=tmp_path, fetch=lambda u, h: (401, {})) == 4
    cap = capsys.readouterr()
    assert "BLOCKED: CDS rejected the API key" in cap.err and FAKE not in cap.out + cap.err
    assert json.loads(cap.out)["cds_key"]["verified"] is False


def test_check_unreachable_store_is_a_network_barrier(tmp_path, capsys):
    (tmp_path / ".cdsapirc").write_text(CDS_RC)

    def fetch(url, headers):
        raise urllib.error.URLError("timed out")

    assert cds.check(False, env={}, home=tmp_path, fetch=fetch) == 2
    assert "BLOCKED: cannot reach the ECMWF cds service" in capsys.readouterr().err


def test_check_without_keys_makes_no_request(tmp_path, capsys):
    def never(u, h):
        raise AssertionError("no key, no request")

    assert cds.check(True, env={}, home=tmp_path, fetch=never) == 4


MALFORMED_CDSAPIRC = {
    "polytope-layout": json.dumps({"user_key": FAKE, "user_email": "fake@example.invalid"}),
    "invalid-json": '{"key": "' + FAKE + '",',
    "empty": "",
    "json-list": "[1, 2]",
    "no-key-line": "url: https://cds.climate.copernicus.eu/api\ntoken: " + FAKE + "\n",
    "not-utf8": b"\xff\xfe\x00\x81",
}


@pytest.mark.parametrize("name", sorted(MALFORMED_CDSAPIRC))
def test_check_malformed_cdsapirc_is_blocked(tmp_path, capsys, name):
    v = MALFORMED_CDSAPIRC[name]
    rc = tmp_path / ".cdsapirc"
    rc.write_bytes(v if isinstance(v, bytes) else v.encode())

    def never(u, h):
        raise AssertionError("no request with a malformed key file")

    assert cds.check(False, env={}, home=tmp_path, fetch=never) == 4
    err = capsys.readouterr().err
    assert "BLOCKED: ~/.cdsapirc is malformed" in err and FAKE not in err
    assert "key: <your personal access token>" in err


def test_malformed_rc_names_keys(tmp_path):
    (tmp_path / ".cdsapirc").write_text(MALFORMED_CDSAPIRC["no-key-line"])
    with pytest.raises(cds.MalformedCredentials) as e:
        cds._token("cds", {}, tmp_path)
    assert e.value.keys == ["token", "url"]


def test_validate_with_malformed_rc_reports_licences_unchecked(tmp_path):
    (tmp_path / ".cdsapirc").write_text("[1, 2]")
    with pytest.raises(cds.MalformedCredentials):
        cds.accepted_licences("cds", env={}, home=tmp_path, fetch=lambda u, h: {})
