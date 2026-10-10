# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
import json
import subprocess
import sys

import pytest
from conftest import SKILLS, load_script

dt = load_script("ecmwf-destine", "destine")
SCRIPT = SKILLS / "ecmwf-destine" / "scripts" / "destine.py"


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
    out = cli("check", "--json", "--offline", home=tmp_path)
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


def test_barriers_destine():
    assert "desp-authentication.py" in "\n".join(
        dt.barrier_for_error("401 Unauthorized")["user_steps"]
    )
    assert "access-policy-upgrade" in "\n".join(dt.barrier_for_error("403 Forbidden")["user_steps"])


def test_cli_retrieve_without_token_is_blocked(tmp_path):
    req = tmp_path / "r.json"
    req.write_text(json.dumps(dt.template("extremes-dt")))
    out = cli("retrieve", str(req), "-o", str(tmp_path / "o"), home=tmp_path)
    assert out.returncode == 4 and "BLOCKED:" in out.stderr
    assert "access-policy-upgrade" in out.stderr


# --- collections endpoint per data bridge -----------------------------------------------------


def test_collections_per_bridge():
    calls = []

    def fetch(url, headers):
        calls.append((url, headers["Authorization"]))
        if "mn5" in url:
            return 200, {"message": ["destination-earth", "test"]}
        if "leonardo" in url:
            raise OSError("unreachable")
        return 200, {"message": ["destination-earth", "ecmwf-destination-earth"]}

    r = dt.collections_by_bridge("tok", fetch=fetch)
    assert r[dt.LUMI] == ["destination-earth", "ecmwf-destination-earth"]
    assert r[dt.MN5] == ["destination-earth", "test"]
    assert "unreachable" in r[dt.LEONARDO]
    assert all(h == "Bearer tok" for _, h in calls)
    assert calls[0][0] == f"https://{dt.LUMI}/api/v1/collections"


def test_collections_rejected_token_is_reported():
    r = dt.collections_by_bridge("bad", fetch=lambda u, h: (401, {"message": "no"}))
    assert all("401" in str(v) for v in r.values())


def test_preflight_blocks_missing_collection():
    req = dt.template("climate-dt")
    b = dt.preflight(req, "tok", fetch=lambda u, h: (200, {"message": ["test"]}))
    assert (
        b
        and "destination-earth" in b["blocked"]
        and "access-policy-upgrade" in "\n".join(b["user_steps"])
    )
    assert (
        dt.preflight(req, "tok", fetch=lambda u, h: (200, {"message": ["destination-earth"]}))
        is None
    )
    b401 = dt.preflight(req, "tok", fetch=lambda u, h: (401, {}))
    assert "desp-authentication.py" in "\n".join(b401["user_steps"])


def test_check_lists_collections_with_token(tmp_path):
    (tmp_path / ".polytopeapirc-destine").write_text('{"user_key": "s3cr3t"}')
    out = cli("check", "--json", "--offline", home=tmp_path)
    d = json.loads(out.stdout)
    assert d["token"] and "collections" in d and "s3cr3t" not in out.stdout


# --- check: every bridge refusing, malformed token files --------------------------------------

FAKE = "zz-fake-token-zz"


def _token(tmp_path, content):
    f = tmp_path / ".polytopeapirc-destine"
    f.write_bytes(content if isinstance(content, bytes) else content.encode())
    return f


def test_check_all_bridges_401_is_blocked(tmp_path, capsys):
    _token(tmp_path, json.dumps({"user_key": FAKE}))
    code = dt.check(True, env={}, home=tmp_path, fetch=lambda u, h: (401, {}))
    cap = capsys.readouterr()
    assert code == 4 and "BLOCKED: DestinE rejected the token" in cap.err
    assert FAKE not in cap.out + cap.err
    assert json.loads(cap.out)["digital_twin_access"] is False


def test_check_without_the_collection_is_blocked(tmp_path, capsys):
    _token(tmp_path, json.dumps({"user_key": FAKE}))
    code = dt.check(True, env={}, home=tmp_path, fetch=lambda u, h: (200, {"message": ["x"]}))
    assert code == 4 and "access-policy-upgrade" in capsys.readouterr().err


def test_check_with_access_on_one_bridge(tmp_path, capsys):
    _token(tmp_path, json.dumps({"user_key": FAKE}))

    def fetch(url, headers):
        return (200, {"message": ["destination-earth"]}) if dt.LUMI in url else (401, {})

    assert dt.check(True, env={}, home=tmp_path, fetch=fetch) == 0
    assert json.loads(capsys.readouterr().out)["digital_twin_access"] is True


def test_check_unreachable_bridges_is_a_network_barrier(tmp_path, capsys, monkeypatch):
    _token(tmp_path, json.dumps({"user_key": FAKE}))
    monkeypatch.setattr(
        dt.ecmwf_status,
        "network_barrier",
        lambda svc, err: {
            "blocked": f"cannot reach the ECMWF {svc} service",
            "why": err,
            "user_steps": ["1. retry"],
            "agent": "retry once",
        },
    )

    def fetch(url, headers):
        raise OSError("Connection refused")

    assert dt.check(False, env={}, home=tmp_path, fetch=fetch) == 2
    assert "cannot reach" in capsys.readouterr().err


MALFORMED_DESTINE = {
    "invalid-json": '{"user_key": "' + FAKE + '",',
    "json-list": "[1, 2]",
    "no-user-key": json.dumps({"key": FAKE}),
    "empty": "",
    "yaml": f"user_key: {FAKE}\n",
    "not-utf8": b"\xff\xfe\x00\x81",
}


@pytest.mark.parametrize("name", sorted(MALFORMED_DESTINE))
def test_malformed_token_file_is_reported(tmp_path, capsys, name):
    _token(tmp_path, MALFORMED_DESTINE[name])
    with pytest.raises(dt.MalformedToken):
        dt.read_token(env={}, home=tmp_path)

    def never(u, h):
        raise AssertionError("no request with a malformed token file")

    assert dt.check(False, env={}, home=tmp_path, fetch=never) == 4
    err = capsys.readouterr().err
    assert "BLOCKED: ~/.polytopeapirc-destine is malformed" in err and FAKE not in err
    assert "desp-authentication.py" in err


def test_retrieve_with_malformed_token_is_blocked(tmp_path):
    _token(tmp_path, "[1, 2]")
    req = tmp_path / "r.json"
    req.write_text(json.dumps(dt.template("climate-dt")))
    out = cli("retrieve", str(req), "-o", str(tmp_path / "o"), home=tmp_path)
    assert out.returncode == 4 and "malformed" in out.stderr and "Traceback" not in out.stderr


def test_grid_is_sent_as_a_dx_dy_string():
    # Polytope rejects a list with equal values: "Duplicate values found in list for key 'grid'".
    assert dt.normalise_grid({"grid": [0.1, 0.1], "param": "167"}) == {
        "grid": "0.1/0.1",
        "param": "167",
    }
    assert dt.normalise_grid({"grid": "0.25/0.25"})["grid"] == "0.25/0.25"
    assert "grid" not in dt.normalise_grid({"param": "167"})


def test_templates_never_carry_a_list_grid():
    for ds in ("climate-dt", "extremes-dt"):
        assert not isinstance(dt.template(ds).get("grid"), list)


def test_retrieve_sends_the_normalised_grid(monkeypatch, tmp_path):
    import types

    sent = {}

    class Client:
        def __init__(self, **k):
            pass

        def retrieve(self, collection, req, output):
            sent.update(req)

    api = types.ModuleType("polytope.api")
    api.Client = Client
    pkg = types.ModuleType("polytope")
    pkg.api = api
    monkeypatch.setitem(sys.modules, "polytope", pkg)
    monkeypatch.setitem(sys.modules, "polytope.api", api)
    monkeypatch.setattr(dt, "read_token", lambda: FAKE)
    dt.retrieve({**dt.template("climate-dt"), "grid": [0.1, 0.1]}, str(tmp_path / "o"))
    assert sent["grid"] == "0.1/0.1"
