# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys

import pytest
from conftest import FIXTURES, SKILLS, load_script

mars = load_script("ecmwf-mars", "mars")
SCRIPT = SKILLS / "ecmwf-mars" / "scripts" / "mars.py"

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


def test_lint_flags_unknown_keywords_with_suggestions():
    req = {**HRES, "level": "500", "dataset": "era5", "format": "grib"}
    errs = "\n".join(mars.lint(req)["errors"])
    assert "'level'" in errs and "levelist" in errs
    assert "'dataset'" in errs and "'format'" in errs


def test_lint_flags_the_typical_hand_written_era5_request():
    text = """retrieve, class=ea, dataset=era5, date=2010-01-01/to/2010-01-31,
      time=00/to/23/by/1, levtype=pl, level=1000/850, param=130, grid=0.25/0.25, format=grib"""
    errs = "\n".join(mars.lint(mars.parse_request(text))["errors"])
    for word in ("'stream'", "'type'", "'level'", "'dataset'", "'format'"):
        assert word in errs, word


def test_verify_webapi_reports_account_not_key(tmp_path):
    (tmp_path / ".ecmwfapirc").write_text(
        json.dumps({"url": "https://api.ecmwf.int/v1", "key": "s3cr3tkey", "email": "a@b.int"})
    )
    seen = {}

    def fetch(url, headers):
        seen.update(url=url, headers=headers)
        return 200, {"uid": "abc", "full_name": "A B", "email": "a@b.int"}

    r = mars.verify_webapi(env={}, home=tmp_path, fetch=fetch)
    assert seen["url"] == "https://api.ecmwf.int/v1/who-am-i"
    assert r == {"verified": True, "account": "abc"}
    assert "s3cr3tkey" not in json.dumps(r)


def test_verify_webapi_rejected_and_missing(tmp_path):
    assert mars.verify_webapi(env={}, home=tmp_path, fetch=None) == {"verified": None}
    (tmp_path / ".ecmwfapirc").write_text('{"url": "u", "key": "k", "email": "e"}')
    r = mars.verify_webapi(env={}, home=tmp_path, fetch=lambda u, h: (403, {}))
    assert r["verified"] is False and "403" in r["error"]


def test_lint_warns_on_uncommon_class_for_operational_streams():
    w = "\n".join(mars.lint({**HRES, "class": "oi"})["warnings"])
    assert "class=oi" in w and "class=od" in w
    assert not any("class=" in x for x in mars.lint(HRES)["warnings"])


def test_lint_warns_two_dates_without_to():
    w = "\n".join(mars.lint({**HRES, "date": "2024-03-01/2024-03-31"})["warnings"])
    assert "2024-03-01/to/2024-03-31" in w


def test_lint_rejects_model_level_numbers_on_pressure_levels():
    errs = "\n".join(mars.lint({**HRES, "levtype": "pl", "levelist": "1/to/137"})["errors"])
    assert "levtype=ml" in errs
    assert mars.lint({**HRES, "levtype": "pl", "levelist": "1000/850/500/1"})["errors"] == []


def test_large_month_is_flagged_but_not_split_below_the_cap():
    era5 = {
        "class": "ea",
        "stream": "oper",
        "type": "an",
        "expver": "1",
        "levtype": "pl",
        "levelist": "all",
        "param": "t",
        "date": "2010-01-01/to/2010-01-31",
        "time": "00/to/23/by/1",
        "grid": "0.25/0.25",
    }
    w = "\n".join(mars.lint(era5)["warnings"])
    assert "GB" in w and "split" not in w and ("grid" in w or "area" in w)
    huge = {**era5, "param": "t/u/v"}
    assert any("split" in x for x in mars.lint(huge)["warnings"])


def test_setup_steps_web_api_and_access_routes():
    s = "\n".join(mars.setup_steps())
    for must in (
        "https://api.ecmwf.int/v1/key/",
        '"url": "https://api.ecmwf.int/v1"',
        "chmod 600 ~/.ecmwfapirc",
        "Computing Representative",
        "https://www.ecmwf.int/en/forecasts/accessing-forecasts/service-agreements",
        "https://support.ecmwf.int",
        "expires",
    ):
        assert must in s, must


def test_cli_check_without_credentials_offers_setup(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "check", "--json"],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert "mars.py setup" in json.loads(out.stdout)["offer"]


def test_barriers_mars():
    rights = mars.barrier_for_error("User 'x' has no access to class=od stream=enfo")
    assert "Computing Representative" in "\n".join(rights["user_steps"])
    key = mars.barrier_for_error("HTTP 403: Invalid API key or key expired")
    assert "https://api.ecmwf.int/v1/key/" in "\n".join(key["user_steps"])
    assert mars.barrier_for_error("No data available") is None


def test_lint_points_tigge_and_s2s_to_ecds():
    errs = "\n".join(mars.lint({**HRES, "class": "ti"})["errors"])
    assert "ecds.ecmwf.int" in errs


def test_cli_retrieve_without_access_is_blocked(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), "retrieve", "retrieve,class=od", "-o", str(tmp_path / "o")],
        capture_output=True,
        text=True,
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
    )
    assert out.returncode == 4 and "BLOCKED:" in out.stderr and "api.ecmwf.int/v1/key" in out.stderr


def test_relative_dates_become_absolute_for_retrieval():
    from datetime import date

    today = date(2026, 10, 4)
    assert mars.absolute_dates({"date": "-2"}, today)["date"] == "2026-10-02"
    assert mars.absolute_dates({"date": "-3/to/-1"}, today)["date"] == "2026-10-01/to/2026-10-03"
    assert mars.absolute_dates({"date": "-1/-2"}, today)["date"] == "2026-10-03/2026-10-02"
    assert (
        mars.absolute_dates({"date": "2024-03-01/to/2024-03-31"}, today)["date"]
        == "2024-03-01/to/2024-03-31"
    )


CERRA = {
    "class": "rr",
    "origin": "se-al-ec",
    "stream": "oper",
    "expver": "prod",
    "type": "an",
    "levtype": "sfc",
    "param": "167",
    "date": "2020-01-01",
    "time": "12",
}


def test_regional_reanalysis_licence_is_copernicus_with_its_doi():
    note = mars.licence_note(CERRA)
    assert "CERRA" in note and "10.24381/cds.622a565a" in note and "ERA5" not in note
    assert "CARRA" in mars.licence_note({**CERRA, "origin": "no-ar-cw"})


def test_regional_reanalysis_size_uses_the_native_lambert_grid():
    assert mars.estimate(CERRA)["points_per_field"] == 1069 * 1069
    assert mars.estimate({**CERRA, "origin": "no-ar-ce"})["points_per_field"] == 789 * 989


def test_lambert_area_without_grid_is_an_error():
    errs = "\n".join(mars.lint({**CERRA, "area": "43/-10/36/-6"})["errors"])
    assert "grid" in errs and "Lambert" in errs
    assert mars.lint({**CERRA, "area": "43/-10/36/-6", "grid": "0.05/0.05"})["errors"] == []


def test_class_rr_needs_an_origin():
    req = {k: v for k, v in CERRA.items() if k != "origin"}
    errs = "\n".join(mars.lint(req)["errors"])
    assert "origin" in errs and "se-al-ec" in errs and "no-ar-cw" in errs


CERRA_ENS = {**CERRA, "stream": "enda", "number": "0/to/9", "time": "00"}


def test_cerra_ensemble_uses_its_own_grid():
    e = mars.estimate(CERRA_ENS)
    assert e["fields"] == 10 and e["points_per_field"] == 565 * 565


def test_cerra_ensemble_needs_number():
    # Without number MARS finds 10 fields and fails: "Expected 1, got 10".
    req = {k: v for k, v in CERRA_ENS.items() if k != "number"}
    assert any("number" in x for x in mars.lint(req)["errors"])
    assert mars.lint(CERRA_ENS)["errors"] == []


def test_cerra_ensemble_area_message_names_the_ensemble_grid():
    errs = "\n".join(mars.lint({**CERRA_ENS, "area": "50/0/40/10"})["errors"])
    assert "11 km" in errs and "ensemble" in errs


def test_values_are_case_insensitive():
    up = {**CERRA, "class": "RR", "type": "FC", "step": "6"}
    r = mars.lint(up)
    assert not any("not a common archive" in w for w in r["warnings"])
    no_step = {k: v for k, v in up.items() if k != "step"}
    assert any("step" in x for x in mars.lint(no_step)["errors"])


def test_metkit_check_returns_none_without_pymetkit(monkeypatch):
    monkeypatch.setitem(sys.modules, "pymetkit", None)  # import raises ImportError
    assert mars.metkit_check("retrieve, class=od") is None


def test_lint_says_values_were_not_checked_without_metkit(monkeypatch):
    monkeypatch.setattr(mars, "metkit_check", lambda text: None)
    r = mars.lint_with_metkit(CERRA)
    assert r["metkit"] == "not installed"
    assert "pymetkit" in r["metkit_hint"]


def test_metkit_errors_are_added_and_shortened(monkeypatch):
    long = "TypeMixed[name=levtype]: cannot expand 'xyz' " + "x" * 500
    monkeypatch.setattr(mars, "metkit_check", lambda text: [mars._short(long)])
    r = mars.lint_with_metkit({**CERRA, "levtype": "xyz"})
    assert any(e.startswith("TypeMixed") and len(e) <= mars.METKIT_MAX for e in r["errors"])
    assert r["metkit"] == "checked"


METKIT = ["uv", "run", "--quiet", "--no-project", "--with", "pymetkit>=1.19,<2", "python3"]


@pytest.mark.earthkit
@pytest.mark.parametrize(
    "req,needle",
    [
        (
            "retrieve, class=od, stream=oper, type=an, levtype=xyz, param=2t, date=2024-01-01, "
            "time=00",
            "levtype",
        ),
        (
            "retrieve, class=od, stream=oper, type=an, levtype=sfc, param=notaparam, "
            "date=2024-01-01, time=00",
            "notaparam",
        ),
        (
            "retrieve, class=od, stream=oper, type=an, levtype=sfc, param=2t, date=2024-02-30, "
            "time=00",
            "20240230",
        ),
    ],
)
def test_lint_with_pymetkit_catches_bad_values(req, needle):
    script = SKILLS / "ecmwf-mars" / "scripts" / "mars.py"
    out = subprocess.run(
        [*METKIT, str(script), "lint", req, "--json"], capture_output=True, text=True, timeout=600
    )
    r = json.loads(out.stdout)  # stdout stays clean JSON despite libeckit's C-level output
    assert out.returncode == 2 and r["metkit"] == "checked"
    assert any(e.startswith("metkit:") and needle in e for e in r["errors"]), r["errors"]


@pytest.mark.earthkit
def test_lint_with_pymetkit_accepts_valid_requests():
    script = SKILLS / "ecmwf-mars" / "scripts" / "mars.py"
    for req in (
        "retrieve, class=rr, origin=se-al-ec, stream=enda, type=an, expver=prod, levtype=sfc, "
        "param=2t, date=2010-01-01, time=00, number=0/to/9, area=50/0/40/10, grid=0.1/0.1",
        "retrieve, class=od, stream=oper, type=fc, expver=1, levtype=sfc, param=2t/10u/10v, "
        "date=2024-03-01/to/2024-03-31, time=00, step=0/to/72/by/6, grid=0.25/0.25, "
        "area=72/-25/30/45",
    ):
        out = subprocess.run(
            [*METKIT, str(script), "lint", req, "--json"],
            capture_output=True,
            text=True,
            timeout=600,
        )
        r = json.loads(out.stdout)
        assert out.returncode == 0 and r["metkit"] == "checked", r["errors"]


def test_interrupted_retrieve_deletes_the_server_job(monkeypatch, tmp_path):
    # ecmwf-api-client deletes its job only after a normal finish; an interrupted client
    # (Ctrl-C, or SIGTERM from a timeout) leaves it queued and blocks the user's next requests.
    import types

    deleted = []

    class Connection:
        def __init__(self, *a, **k):
            self.location = "https://api.ecmwf.int/v1/services/mars/requests/abc"

        def cleanup(self):
            deleted.append(self.location)

    api = types.ModuleType("ecmwfapi.api")
    api.Connection = Connection
    pkg = types.ModuleType("ecmwfapi")
    pkg.api = api

    def from_source(name, req):
        Connection()
        raise KeyboardInterrupt

    ekd = types.ModuleType("earthkit.data")
    ekd.from_source = from_source
    ek = types.ModuleType("earthkit")
    ek.data = ekd
    monkeypatch.setitem(sys.modules, "ecmwfapi", pkg)
    monkeypatch.setitem(sys.modules, "ecmwfapi.api", api)
    monkeypatch.setitem(sys.modules, "earthkit", ek)
    monkeypatch.setitem(sys.modules, "earthkit.data", ekd)
    with pytest.raises(KeyboardInterrupt):
        mars.retrieve(CERRA, str(tmp_path / "x.grib"))
    assert deleted == ["https://api.ecmwf.int/v1/services/mars/requests/abc"]
    assert Connection.__init__.__name__ == "__init__"  # patch removed afterwards


def test_sigterm_becomes_an_interrupt():
    import signal

    with pytest.raises(KeyboardInterrupt):
        mars._interrupt(signal.SIGTERM, None)


# --- malformed credentials, key prompt, MARS rights probe -----------------------------------------

FAKE_KEY, FAKE_EMAIL = "zz-fake-key-zz", "fake@example.invalid"
MALFORMED_RC = {
    "polytope-layout": json.dumps({"user_key": FAKE_KEY, "user_email": FAKE_EMAIL}),
    "no-url": json.dumps({"key": FAKE_KEY, "email": FAKE_EMAIL}),
    "invalid-json": '{"url": "https://api.ecmwf.int/v1", "key": "' + FAKE_KEY + '",',
    "empty": "",
    "cds-yaml": f"url: https://cds.climate.copernicus.eu/api\nkey: {FAKE_KEY}\n",
    "json-list": "[1, 2]",
}


@pytest.mark.parametrize("name", sorted(MALFORMED_RC))
def test_malformed_ecmwfapirc_is_reported_not_raised(tmp_path, name):
    (tmp_path / ".ecmwfapirc").write_text(MALFORMED_RC[name])
    with pytest.raises(mars.MalformedCredentials) as e:
        mars._webapi_settings({}, tmp_path)
    b = mars.malformed_barrier(e.value)
    text = json.dumps(b)
    assert "~/.ecmwfapirc is malformed" in b["blocked"]
    assert FAKE_KEY not in text and FAKE_EMAIL not in text
    assert '"url": "https://api.ecmwf.int/v1"' in "\n".join(b["user_steps"])


def test_malformed_rc_names_the_keys_found_and_the_polytope_layout(tmp_path):
    (tmp_path / ".ecmwfapirc").write_text(MALFORMED_RC["polytope-layout"])
    with pytest.raises(mars.MalformedCredentials) as e:
        mars._webapi_settings({}, tmp_path)
    b = mars.malformed_barrier(e.value)
    assert "user_email, user_key" in b["why"]
    steps = "\n".join(b["user_steps"])
    assert "~/.polytopeapirc" in steps and "both" in steps


def test_rc_that_is_not_utf8(tmp_path):
    (tmp_path / ".ecmwfapirc").write_bytes(b"\xff\xfe\x00\x81bad")
    with pytest.raises(mars.MalformedCredentials, match="UTF-8"):
        mars._webapi_settings({}, tmp_path)


def test_yaml_rc_lists_key_names_only(tmp_path):
    (tmp_path / ".ecmwfapirc").write_text(MALFORMED_RC["cds-yaml"])
    with pytest.raises(mars.MalformedCredentials) as e:
        mars._webapi_settings({}, tmp_path)
    assert e.value.keys == ["key", "url"] and "not JSON" in e.value.problem


def test_user_key_is_not_accepted_as_an_alias(tmp_path):
    both = {"user_key": FAKE_KEY, "user_email": FAKE_EMAIL, "url": "https://api.ecmwf.int/v1"}
    (tmp_path / ".ecmwfapirc").write_text(json.dumps(both))
    with pytest.raises(mars.MalformedCredentials, match="email, key"):
        mars._webapi_settings({}, tmp_path)


def test_rc_with_both_layouts_is_fine(tmp_path):
    both = {
        "url": "https://api.ecmwf.int/v1",
        "key": FAKE_KEY,
        "email": FAKE_EMAIL,
        "user_key": FAKE_KEY,
        "user_email": FAKE_EMAIL,
    }
    (tmp_path / ".ecmwfapirc").write_text(json.dumps(both))
    assert mars._webapi_settings({}, tmp_path)["url"] == "https://api.ecmwf.int/v1"


def test_partial_environment_is_reported(tmp_path):
    with pytest.raises(mars.MalformedCredentials) as e:
        mars._webapi_settings({"ECMWF_API_KEY": FAKE_KEY}, tmp_path)
    assert e.value.source == "ECMWF_API_* environment"
    assert "ECMWF_API_URL" in e.value.problem and "ECMWF_API_EMAIL" in e.value.problem
    assert FAKE_KEY not in json.dumps(mars.malformed_barrier(e.value))


def test_partial_environment_wins_over_a_valid_rc(tmp_path):
    # ecmwf-api-client raises "Incomplete API key found in the environment" instead of
    # falling back to the file, so the file does not rescue it.
    good = {"url": "https://api.ecmwf.int/v1", "key": FAKE_KEY, "email": FAKE_EMAIL}
    (tmp_path / ".ecmwfapirc").write_text(json.dumps(good))
    with pytest.raises(mars.MalformedCredentials, match="ECMWF_API_EMAIL"):
        mars._webapi_settings({"ECMWF_API_KEY": "x", "ECMWF_API_URL": "u"}, tmp_path)


def _cli(home, *args, env=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        # a dead proxy: any accidental network call fails fast instead of going online
        env={"HOME": str(home), "PATH": "/usr/bin:/bin", "https_proxy": "http://127.0.0.1:9"}
        | (env or {}),
    )


def test_cli_check_malformed_rc_is_blocked_without_traceback(tmp_path):
    (tmp_path / ".ecmwfapirc").write_text(MALFORMED_RC["polytope-layout"])
    out = _cli(tmp_path, "check")
    assert out.returncode == 4, out.stderr
    assert "Traceback" not in out.stderr and "BLOCKED: ~/.ecmwfapirc is malformed" in out.stderr
    assert FAKE_KEY not in out.stderr + out.stdout


def test_cli_check_json_malformed_rc_stdout_is_one_json_document(tmp_path):
    (tmp_path / ".ecmwfapirc").write_text("[1, 2]")
    out = _cli(tmp_path, "check", "--json")
    assert out.returncode == 4 and "malformed" in json.loads(out.stdout)["blocked"]["blocked"]


def test_cli_cost_with_malformed_rc_is_blocked_before_the_client(tmp_path):
    (tmp_path / ".ecmwfapirc").write_text(MALFORMED_RC["no-url"])
    req = "retrieve,class=od,stream=oper,type=an,levtype=sfc,param=2t,date=-1,time=00"
    out = _cli(tmp_path, "cost", req)
    assert out.returncode == 4 and "malformed" in out.stderr and "Traceback" not in out.stderr


def test_key_prompt_without_a_terminal_is_a_key_barrier(monkeypatch, capsys, tmp_path):
    # earthkit-data asks for the key interactively when it finds none; without a terminal
    # input() raises EOFError.
    monkeypatch.setattr(mars, "credentials", lambda: {"webapi": None, "mars_client": "mars"})

    def prompt(req, output):
        raise EOFError("EOF when reading a line")

    monkeypatch.setattr(mars, "retrieve", prompt)
    req = "retrieve,class=od,stream=oper,type=an,levtype=sfc,param=2t,date=-1,time=00,expver=1"
    assert mars.main(["retrieve", req, "-o", str(tmp_path / "x.grib")]) == 4
    err = capsys.readouterr().err
    assert "BLOCKED:" in err and "api.ecmwf.int/v1/key" in err


def test_probe_confirms_mars_rights():
    seen = {}

    def cost(req):
        seen.update(req)
        return {"size": 1, "number_of_fields": 1}

    r = mars.probe_mars(cost=cost)
    assert r["mars_rights"] == "confirmed" and r["probe_seconds"] >= 0
    assert seen["class"] == "od" and seen["param"] == "2t" and seen["date"] == "-1"


def test_probe_without_rights_is_a_barrier():
    def cost(req):
        raise RuntimeError("ecmwf.API error 1: User 'x' has no access to services/mars")

    r = mars.probe_mars(cost=cost)
    assert r["mars_rights"] == "none"
    assert "Computing Representative" in "\n".join(r["blocked"]["user_steps"])


def test_probe_timeout_is_unknown():
    def cost(req):
        raise mars.ProbeTimeout("no answer within 120 s")

    r = mars.probe_mars(cost=cost)
    assert r["mars_rights"] == "unknown" and "120 s" in r["reason"]


def test_probe_without_the_client_names_the_uv_command():
    def cost(req):
        raise ImportError("No module named 'ecmwfapi'")

    r = mars.probe_mars(cost=cost)
    assert r["mars_rights"] == "unknown" and "uv run" in r["reason"] and "--probe" in r["reason"]


def test_probe_timeout_cancels_the_server_job(monkeypatch):
    import types

    deleted = []

    class Connection:
        def __init__(self, *a, **k):
            self.location = "https://api.ecmwf.int/v1/services/mars/requests/p1"

        def cleanup(self):
            deleted.append(self.location)

    class ECMWFService:
        def __init__(self, *a, **k):
            pass

        def execute(self, text, target):
            Connection()
            raise mars.ProbeTimeout("no answer within 120 s")

    api = types.ModuleType("ecmwfapi.api")
    api.Connection = Connection
    pkg = types.ModuleType("ecmwfapi")
    pkg.api, pkg.ECMWFService = api, ECMWFService
    monkeypatch.setitem(sys.modules, "ecmwfapi", pkg)
    monkeypatch.setitem(sys.modules, "ecmwfapi.api", api)
    r = mars.probe_mars()
    assert r["mars_rights"] == "unknown"
    assert deleted == ["https://api.ecmwf.int/v1/services/mars/requests/p1"]


def test_check_probe_runs_after_a_verified_key(monkeypatch, capsys):
    monkeypatch.setattr(
        mars, "credentials", lambda: {"webapi": "~/.ecmwfapirc", "mars_client": None}
    )
    monkeypatch.setattr(mars, "_webapi_settings", lambda env, home: {"url": "u"})
    monkeypatch.setattr(mars, "verify_webapi", lambda: {"verified": True, "account": "abc"})
    monkeypatch.setattr(
        mars, "probe_mars", lambda: {"mars_rights": "confirmed", "probe_seconds": 3}
    )
    assert mars.main(["check", "--probe", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["mars_rights"] == "confirmed"


def test_check_probe_blocked_exits_4(monkeypatch, capsys):
    monkeypatch.setattr(
        mars, "credentials", lambda: {"webapi": "~/.ecmwfapirc", "mars_client": None}
    )
    monkeypatch.setattr(mars, "_webapi_settings", lambda env, home: {"url": "u"})
    monkeypatch.setattr(mars, "verify_webapi", lambda: {"verified": True, "account": "abc"})
    b = mars.barrier_for_error("has no access to services/mars")
    monkeypatch.setattr(mars, "probe_mars", lambda: {"mars_rights": "none", "blocked": b})
    assert mars.main(["check", "--probe"]) == 4
    assert "BLOCKED:" in capsys.readouterr().err


def test_plain_check_does_not_probe(monkeypatch, capsys):
    monkeypatch.setattr(
        mars, "credentials", lambda: {"webapi": "~/.ecmwfapirc", "mars_client": None}
    )
    monkeypatch.setattr(mars, "_webapi_settings", lambda env, home: {"url": "u"})
    monkeypatch.setattr(mars, "verify_webapi", lambda: {"verified": True, "account": "abc"})

    def never():
        raise AssertionError("plain check must stay fast")

    monkeypatch.setattr(mars, "probe_mars", never)
    assert mars.main(["check"]) == 0
    assert "--probe" in capsys.readouterr().out


def test_wrapped_key_prompt_failure_is_a_key_barrier(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(mars, "credentials", lambda: {"webapi": None, "mars_client": "mars"})

    def prompt(req, output):
        raise RuntimeError("source failed: EOF when reading a line")

    monkeypatch.setattr(mars, "retrieve", prompt)
    req = "retrieve,class=od,stream=oper,type=an,levtype=sfc,param=2t,date=-1,time=00,expver=1"
    assert mars.main(["retrieve", req, "-o", str(tmp_path / "x.grib")]) == 4
    assert "no usable ECMWF Web API key" in capsys.readouterr().err


def test_rc_file_env_pointing_nowhere_is_reported(tmp_path):
    with pytest.raises(mars.MalformedCredentials, match="does not exist"):
        mars._webapi_settings({"ECMWF_API_RC_FILE": str(tmp_path / "nope")}, tmp_path)


def test_probe_unreachable_is_unknown_with_a_network_report(monkeypatch):
    monkeypatch.setattr(
        mars.ecmwf_status,
        "network_barrier",
        lambda svc, err: {"blocked": "cannot reach", "why": err, "user_steps": [], "agent": ""},
    )

    def cost(req):
        raise OSError("Connection refused")

    r = mars.probe_mars(cost=cost)
    assert r["mars_rights"] == "unknown" and r["blocked"]["blocked"] == "cannot reach"
