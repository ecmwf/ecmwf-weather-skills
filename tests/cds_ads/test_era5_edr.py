# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0
"""ERA5 point series from ECMWF's OGC API EDR service (`cds.py era5-point --route`).

Offline tests use one recorded EDR answer (Paris, 14-15 August 2003, t2m/tp/sst) and the CSV
the CDS timeseries route wrote for the same request; network calls are injected.

  uv run pytest tests/cds_ads/test_era5_edr.py
  uv run pytest -m live tests/cds_ads      # real EDR and CDS (the CDS part needs a key and uv)
"""

import csv
import io
import json
import subprocess
import sys
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from conftest import FIXTURES, SKILLS, load_script

cds = load_script("ecmwf-cds-ads", "cds")
SCRIPT = SKILLS / "ecmwf-cds-ads" / "scripts" / "cds.py"
COLLECTION = (FIXTURES / "era5-edr-collection.json").read_bytes()
COVERAGE = (FIXTURES / "era5-edr-paris-2003-08-14.covjson").read_bytes()
CDS_CSV = (FIXTURES / "era5-cds-paris-2003-08-14.csv").read_text()
FORM_TS = json.loads((FIXTURES / "cds-form-era5-ts.json").read_text())
# The recorded request: CDS names in another order than the CSV columns.
PARIS = ["total_precipitation", "sea_surface_temperature", "2m_temperature"]
UTC = timezone.utc
COLL_END = datetime(2026, 10, 1, 23, tzinfo=UTC)  # the recorded collection's last time
ARGS = ["era5-point", "--lat", "48.85", "--lon", "2.35", "--start", "2003-08-14"]
ARGS += ["--end", "2003-08-15", "--variable", ",".join(PARIS)]


def fake_edr(coverage=COVERAGE, collection=COLLECTION, seen=None):
    """fetch(url, timeout) answering the collection document and one recorded coverage."""

    def fetch(url, timeout):
        if seen is not None:
            seen.append(url)
        answer = coverage if "/position?" in url else collection
        if isinstance(answer, Exception):
            raise answer
        return answer

    return fetch


def http_error(code):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(b"{}"))


def paris(out, fetch, lon=2.35):
    return cds.fetch_edr(48.85, lon, PARIS, "2003-08-14", "2003-08-15", out, fetch=fetch)


# --- names, columns, numbers ---------------------------------------------------------------------


def test_short_names_are_the_edr_parameters_and_cds_variables():
    edr = json.loads(COLLECTION)["parameter_names"]
    assert set(cds.ERA5_SHORT.values()) == set(edr)
    assert set(cds.ERA5_SHORT) <= set(cds.form_options(FORM_TS)["variable"]["values"])


def test_columns_follow_the_cds_csv_order_not_the_request_order():
    # The CDS writes columns sorted by CDS variable name (recorded: tp,sst,t2m -> t2m,sst,tp).
    cols, missing = cds.edr_columns([*PARIS, "2m_temperature"])
    assert cols == ["t2m", "sst", "tp"] and missing == []
    assert CDS_CSV.splitlines()[0] == "valid_time,t2m,sst,tp,latitude,longitude"


def test_variables_without_an_edr_parameter_are_reported():
    assert cds.edr_columns(["2m_temperature", "mean_wave_period"]) == (
        ["t2m"],
        ["mean_wave_period"],
    )


# numpy's str() of float32 values — how the CDS writes its CSV values.
NUMPY_FLOAT32_STR = [
    (289.66839599609375, "289.6684"),
    (1208935.5, "1.2089355e+06"),
    (101984.44, "101984.44"),
    (0.085494995, "0.085494995"),
    (5333.0, "5333.0"),
    (-1201.0, "-1201.0"),
    (0.0, "0.0"),
    (-0.0, "-0.0"),
    (9.5367431640625e-07, "9.536743e-07"),
    (1.2e-05, "1.2e-05"),
    (1e-4, "1e-04"),
    (0.000100000005, "0.000100000005"),
    (999999.94, "999999.94"),
    (1e6, "1e+06"),
    (1000000.0625, "1.00000006e+06"),
    (16777218.0, "1.6777218e+07"),
    (1 / 3, "0.33333334"),
    (2.0**87, "1.5474251e+26"),  # a power of two: its shortest decimal lies above it
    (1.4e-45, "1e-45"),
    (3.4028235e38, "3.4028235e+38"),
]


@pytest.mark.parametrize(
    ("value", "text"), NUMPY_FLOAT32_STR, ids=[t for _, t in NUMPY_FLOAT32_STR]
)
def test_float32_text_matches_numpy(value, text):
    assert cds.f32_str(value) == text


# --- the request plan ----------------------------------------------------------------------------


def test_url_names_the_era5_collection_and_point():
    t0, t1 = datetime(2003, 8, 1, tzinfo=UTC), datetime(2003, 8, 31, 23, tzinfo=UTC)
    assert cds.edr_url(48.85, 2.35, ["t2m", "tp"], t0, t1) == (
        "https://polytope-edr.lumi.apps.dte.destination-earth.eu/collections/era5-single-levels/"
        "position?f=CoverageJSON&coords=POINT(2.35%2048.85)&parameter-name=t2m,tp"
        "&datetime=2003-08-01T00:00:00Z/2003-08-31T23:00:00Z"
    )


def _spans(chunks):
    return [(a.strftime("%Y-%m-%d %H"), b.strftime("%Y-%m-%d %H")) for a, b in chunks]


def test_one_request_when_it_fits():
    # 30 years of one hourly variable came back in one request in seconds.
    assert _spans(cds.edr_chunks("1991-01-01", "2020-12-31", 1)) == [
        ("1991-01-01 00", "2020-12-31 23")
    ]
    assert _spans(cds.edr_chunks("2003-12-15", "2004-01-15", 20)) == [
        ("2003-12-15 00", "2004-01-15 23")
    ]


def test_long_requests_are_split_at_year_boundaries():
    assert _spans(cds.edr_chunks("1991-01-01", "2020-12-31", 2)) == [
        ("1991-01-01 00", "2005-12-31 23"),
        ("2006-01-01 00", "2020-12-31 23"),
    ]
    chunks = cds.edr_chunks("2001-03-01", "2003-12-31", 20)
    assert _spans(chunks) == [
        ("2001-03-01 00", "2001-12-31 23"),
        ("2002-01-01 00", "2002-12-31 23"),
        ("2003-01-01 00", "2003-12-31 23"),
    ]
    hours = sum((b - a) // timedelta(hours=1) + 1 for a, b in chunks)
    assert hours == (datetime(2004, 1, 1) - datetime(2001, 3, 1)) // timedelta(hours=1)
    # Whole years per request, as many as fit: 2002 and half of 2003 share one.
    assert _spans(cds.edr_chunks("2001-03-01", "2003-06-30", 20))[1] == (
        "2002-01-01 00",
        "2003-06-30 23",
    )


def test_collection_extent_and_parameters():
    c = cds.edr_collection(fetch=fake_edr())
    assert c["start"] == datetime(1940, 1, 1, tzinfo=UTC) and c["end"] == COLL_END
    assert c["parameters"]["t2m"] == "K" and len(c["parameters"]) == 20


@pytest.mark.parametrize(
    "doc", [b"<html>maintenance</html>", b"{}", b'{"extent": {"temporal": {"interval": []}}}']
)
def test_malformed_collection_document_is_an_edr_error(doc):
    with pytest.raises(cds.EdrError):
        cds.edr_collection(fetch=fake_edr(collection=doc))


# --- route decisions -----------------------------------------------------------------------------


def _coll():
    return cds.edr_collection(fetch=fake_edr())


def test_auto_uses_the_edr_when_it_can_serve_the_request():
    route, why = cds.choose_route("auto", PARIS, "csv", "2003-08-14", "2003-08-15", _coll())
    assert route == "edr" and "2026-10-01T23:00Z" in why


@pytest.mark.parametrize(
    ("variables", "fmt", "start", "end", "reason"),
    [
        (["mean_wave_period"], "csv", "2003-08-14", "2003-08-15", "mean_wave_period"),
        (PARIS, "netcdf", "2003-08-14", "2003-08-15", "CSV"),
        (PARIS, "csv", "2026-09-25", "2026-10-02", "2026-10-01T23:00Z"),
        (PARIS, "csv", "1939-12-31", "1940-01-02", "1940-01-01"),
    ],
    ids=["variable", "netcdf", "after-end", "before-start"],
)
def test_auto_uses_the_cds_and_says_why(variables, fmt, start, end, reason):
    route, why = cds.choose_route("auto", variables, fmt, start, end, _coll())
    assert route == "cds" and reason in why


def test_auto_uses_the_cds_when_the_collection_drops_a_parameter():
    coll = _coll()
    del coll["parameters"]["sst"]
    route, why = cds.choose_route("auto", PARIS, "csv", "2003-08-14", "2003-08-15", coll)
    assert route == "cds" and "sst" in why


def test_auto_uses_the_cds_when_the_edr_does_not_answer():
    err = cds.EdrError("HTTP 503", status=503)
    route, why = cds.choose_route("auto", PARIS, "csv", "2003-08-14", "2003-08-15", err)
    assert route == "cds" and "503" in why


def test_route_cds_never_needs_the_edr():
    assert cds.choose_route("cds", PARIS, "csv", "2003-08-14", "2003-08-15", None)[0] == "cds"


def test_route_edr_refuses_what_the_edr_cannot_serve():
    with pytest.raises(cds.RouteError, match="mean_wave_period"):
        cds.choose_route("edr", ["mean_wave_period"], "csv", "2003-08-14", "2003-08-15", _coll())
    with pytest.raises(cds.RouteError, match="2026-10-01T23:00Z"):
        cds.choose_route("edr", PARIS, "csv", "2026-09-30", "2026-10-03", _coll())


# --- the CSV -------------------------------------------------------------------------------------


def test_edr_csv_is_identical_to_the_cds_csv(tmp_path):
    out = tmp_path / "paris.csv"
    r = paris(out, fake_edr())
    assert out.read_text() == CDS_CSV
    assert r["gridpoint"] == {"lat": 48.75, "lon": 2.25} and r["rows"] == 48
    assert r["requests"] == 1 and r["units"] == {"t2m": "K", "sst": "K", "tp": "m"}


def test_longitudes_are_written_from_minus_180_to_180(tmp_path):
    doc = json.loads(COVERAGE)
    doc["domain"]["axes"]["x"]["values"] = [357.75]
    out = tmp_path / "x.csv"
    r = paris(out, fake_edr(json.dumps(doc).encode()), lon=-2.3)
    assert r["gridpoint"]["lon"] == -2.25 and out.read_text().splitlines()[1].endswith(",-2.25")


def test_only_the_era5_collection_is_requested(tmp_path):
    seen = []
    cds.edr_collection(fetch=fake_edr(seen=seen))
    paris(tmp_path / "p.csv", fake_edr(seen=seen))
    assert len(seen) == 2
    assert all(u.startswith(f"{cds.EDR_URL}/collections/era5-single-levels") for u in seen)


def _broken(change):
    doc = json.loads(COVERAGE)
    change(doc)
    return json.dumps(doc).encode()


def _drop_last_hour(doc):
    doc["domain"]["axes"]["t"]["values"].pop()
    for r in doc["ranges"].values():
        r["values"].pop()


BROKEN = {
    "html": b"<html>Bad gateway</html>",
    "not-a-point-series": _broken(lambda d: d.update(domainType="Grid")),
    "missing-parameter": _broken(lambda d: d["ranges"].pop("sst")),
    "values-and-times-differ": _broken(lambda d: d["domain"]["axes"]["t"]["values"].pop()),
    "incomplete-period": _broken(_drop_last_hour),
    "text-value": _broken(lambda d: d["ranges"]["t2m"]["values"].__setitem__(3, "x")),
    "beyond-float32": _broken(lambda d: d["ranges"]["t2m"]["values"].__setitem__(3, 1e300)),
    "far-gridpoint": _broken(lambda d: d["domain"]["axes"]["y"].update(values=[2.25])),
}


@pytest.mark.parametrize("name", sorted(BROKEN))
def test_malformed_or_incomplete_answers_are_edr_errors(tmp_path, name):
    with pytest.raises(cds.EdrError):
        paris(tmp_path / "p.csv", fake_edr(BROKEN[name]))
    assert list(tmp_path.iterdir()) == []  # no partial file


@pytest.mark.parametrize("code", [401, 403, 404, 500, 502, 503])
def test_http_errors_are_edr_errors_with_the_status(tmp_path, code):
    with pytest.raises(cds.EdrError) as e:
        paris(tmp_path / "p.csv", fake_edr(http_error(code)))
    assert e.value.status == code


@pytest.mark.parametrize("err", [TimeoutError("timed out"), urllib.error.URLError("refused")])
def test_unreachable_service_is_an_edr_error(tmp_path, err):
    with pytest.raises(cds.EdrError) as e:
        paris(tmp_path / "p.csv", fake_edr(err))
    assert e.value.network


# --- CLI -----------------------------------------------------------------------------------------


@pytest.fixture
def offline_cds(monkeypatch):
    """The CDS route without the network: validation, licences and the download are recorded."""
    downloads = []
    coll = {"license": "CC-BY-4.0", "sci:doi": "10.24381/1cf1ad76"}
    monkeypatch.setattr(cds, "get_collection", lambda s, d: coll)
    monkeypatch.setattr(cds, "get_form", lambda c: FORM_TS)
    monkeypatch.setattr(cds, "costing", lambda s, d, r: {"cost": 108.0, "limit": 760.0})
    monkeypatch.setattr(cds, "accepted_licences", lambda s: {"licences": [{"id": "cc-by"}]})
    monkeypatch.setattr(cds, "credentials", lambda store, *a, **k: "~/.cdsapirc")

    def retrieve(store, dataset, request, output):
        downloads.append(request)
        Path(output).write_text(CDS_CSV)
        return [Path(output)]

    monkeypatch.setattr(cds, "retrieve", retrieve)
    return downloads


def test_cli_auto_uses_the_edr_without_a_key(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cds, "_edr_get", fake_edr())
    monkeypatch.setattr(cds, "credentials", lambda *a, **k: None)  # no CDS key on this machine
    out = tmp_path / "paris.csv"
    assert cds.main([*ARGS, "-o", str(out), "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["route"] == "edr" and r["gridpoint"] == {"lat": 48.75, "lon": 2.25}
    assert r["doi"] == "10.24381/cds.adbb2d47" and r["licence"] == "CC-BY-4.0"
    assert r["attribution"] == (
        f"Generated using Copernicus Climate Change Service information {datetime.now(UTC).year}. "
        "Neither the European Commission nor ECMWF is responsible for any use that may be made of "
        "the Copernicus information or data it contains."
    )
    assert r["output"] == [str(out)] and out.read_text() == CDS_CSV


def test_cli_text_names_route_gridpoint_attribution_and_doi(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cds, "_edr_get", fake_edr())
    assert cds.main([*ARGS, "-o", str(tmp_path / "p.csv")]) == 0
    text = capsys.readouterr().out
    for must in (
        "route: edr",
        "gridpoint: 48.75, 2.25",
        "Copernicus Climate Change Service",
        "DOI: 10.24381/cds.adbb2d47",
    ):
        assert must in text, must


FAILURES = {
    "401": http_error(401),
    "403": http_error(403),
    "404": http_error(404),
    "503": http_error(503),
    "timeout": TimeoutError("timed out"),
    "malformed": b"<html/>",
}


@pytest.mark.parametrize("name", sorted(FAILURES))
def test_cli_auto_falls_back_to_the_cds_with_a_note(
    tmp_path, monkeypatch, capsys, offline_cds, name
):
    monkeypatch.setattr(cds, "_edr_get", fake_edr(coverage=FAILURES[name]))
    out = tmp_path / "paris.csv"
    assert cds.main([*ARGS, "-o", str(out), "--json"]) == 0
    cap = capsys.readouterr()
    r = json.loads(cap.out)
    assert r["route"] == "cds" and "EDR" in r["route_reason"] and "EDR" in cap.err
    assert r["gridpoint"] == {"lat": 48.75, "lon": 2.25} and r["doi"] == "10.24381/1cf1ad76"
    assert len(offline_cds) == 1 and out.read_text() == CDS_CSV


def test_cli_auto_without_edr_or_key_is_blocked_and_says_why(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cds, "_edr_get", fake_edr(collection=http_error(503)))
    monkeypatch.setattr(cds, "credentials", lambda *a, **k: None)
    assert cds.main([*ARGS, "-o", str(tmp_path / "p.csv")]) == 4
    err = capsys.readouterr().err
    assert "503" in err and "BLOCKED: no CDS API key" in err


def test_cli_route_cds_makes_no_edr_request(tmp_path, monkeypatch, capsys, offline_cds):
    def never(url, timeout):
        raise AssertionError("--route cds must not call the EDR")

    monkeypatch.setattr(cds, "_edr_get", never)
    assert cds.main([*ARGS, "--route", "cds", "-o", str(tmp_path / "p.csv"), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["route"] == "cds"


def test_cli_edr_dry_run_shows_the_plan_without_data_requests(monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(cds, "_edr_get", fake_edr(seen=seen))
    assert cds.main([*ARGS, "--route", "edr", "--dry-run", "--json"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["route"] == "edr" and len(r["edr"]["requests"]) == 1
    url = r["edr"]["requests"][0]["url"]
    assert url.startswith(f"{cds.EDR_URL}/collections/era5-single-levels/position?")
    assert seen and all("/position?" not in u for u in seen)  # only the collection was read


def test_cli_auto_dry_run_shows_the_edr_plan_and_the_cds_fallback(monkeypatch, capsys, offline_cds):
    monkeypatch.setattr(cds, "_edr_get", fake_edr())
    assert cds.main([*ARGS, "--dry-run"]) == 0
    text = capsys.readouterr().out
    assert "route: edr" in text and "/position?" in text
    assert "reanalysis-era5-single-levels-timeseries" in text and "\nvalid\n" in text
    # Each route cites its own dataset DOI.
    assert "DOI (EDR route): 10.24381/cds.adbb2d47" in text
    assert "DOI (CDS route): 10.24381/1cf1ad76" in text
    assert offline_cds == []  # nothing downloaded


def test_cli_route_edr_access_refused_is_blocked(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cds, "_edr_get", fake_edr(coverage=http_error(403)))
    assert cds.main([*ARGS, "--route", "edr", "-o", str(tmp_path / "p.csv")]) == 4
    err = capsys.readouterr().err
    assert "BLOCKED:" in err and "--route cds" in err and "cds.py setup" in err


def test_cli_route_edr_unreachable_is_a_network_barrier(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cds, "_edr_get", fake_edr(collection=urllib.error.URLError("timed out")))
    assert cds.main([*ARGS, "--route", "edr", "-o", str(tmp_path / "p.csv")]) == 2
    assert "BLOCKED: cannot reach the ECMWF era5-edr service" in capsys.readouterr().err


def test_cli_route_edr_rejects_a_variable_it_lacks(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cds, "_edr_get", fake_edr())
    args = [*ARGS[:-1], "mean_wave_period", "--route", "edr", "-o", str(tmp_path / "p.csv")]
    assert cds.main(args) == 2
    assert "mean_wave_period" in capsys.readouterr().err


# --- live: the real EDR and CDS ------------------------------------------------------------------

needs_cds = pytest.mark.skipif(
    cds.credentials("cds") is None, reason="no CDS key (~/.cdsapirc or CDSAPI_KEY) on this machine"
)
# The CDS route queues the request (~40 s when the queue is free) and installs earthkit-data.
CDS_TIMEOUT_S = 900


def _uv(*args, timeout=CDS_TIMEOUT_S):
    return subprocess.run(
        ["uv", "run", "--quiet", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _rows(path):
    with open(path, newline="") as f:
        return list(csv.reader(f))


@pytest.mark.live
def test_live_edr_collection_serves_every_mapped_variable():
    c = cds.edr_collection()
    assert set(cds.ERA5_SHORT.values()) <= set(c["parameters"])
    assert timedelta(0) < datetime.now(UTC) - c["end"] < timedelta(days=40)


@pytest.mark.live
def test_live_auto_dry_run_plans_the_edr(tmp_path):
    out = subprocess.run(
        [sys.executable, str(SCRIPT), *ARGS, "--dry-run", "--json"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert out.returncode == 0, out.stderr
    r = json.loads(out.stdout)
    assert r["route"] == "edr" and r["cds"]["valid"]


@pytest.mark.live
@needs_cds
def test_live_edr_and_cds_routes_give_the_same_values(tmp_path):
    args = [*ARGS[:-1], "2m_temperature,total_precipitation", "--json"]
    edr = subprocess.run(
        [sys.executable, str(SCRIPT), *args, "--route", "edr", "-o", str(tmp_path / "edr.csv")],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert edr.returncode == 0, edr.stderr
    via_cds = _uv(*args, "--route", "cds", "-o", str(tmp_path / "cds.csv"))
    assert via_cds.returncode == 0, via_cds.stderr
    assert json.loads(edr.stdout)["route"] == "edr" and json.loads(via_cds.stdout)["route"] == "cds"
    a, b = _rows(tmp_path / "edr.csv"), _rows(tmp_path / "cds.csv")
    assert a[0] == b[0] == ["valid_time", "t2m", "tp", "latitude", "longitude"]
    assert len(a) == len(b) == 49
    for ra, rb in zip(a[1:], b[1:], strict=True):
        assert ra[0] == rb[0] and ra[3:] == rb[3:]
        assert all(abs(float(x) - float(y)) <= 1e-3 for x, y in zip(ra[1:3], rb[1:3], strict=True))


@pytest.mark.live
@needs_cds
def test_live_auto_falls_back_to_the_cds_for_a_variable_the_edr_lacks(tmp_path):
    out = tmp_path / "waves.csv"
    run = _uv(
        "era5-point",
        "--lat",
        "45",
        "--lon",
        "-30",
        "--start",
        "2003-08-14",
        "--end",
        "2003-08-14",
        "--variable",
        "mean_wave_period",
        "-o",
        str(out),
        "--json",
    )
    assert run.returncode == 0, run.stderr
    r = json.loads(run.stdout)
    assert r["route"] == "cds" and "mean_wave_period" in r["route_reason"]
    rows = _rows(out)
    assert rows[0][0] == "valid_time" and rows[0][-2:] == ["latitude", "longitude"]
    assert len(rows) == 25
