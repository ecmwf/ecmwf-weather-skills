# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
# SPDX-License-Identifier: Apache-2.0
"""destine.py against ECMWF's DestinE data catalogue (Omnicat): every request is validated and
normalised by the catalogue, invalid axes are named with their valid values, and `options`
lists what exists. Offline tests use recorded answers and a small model of the catalogue;
`-m live` asks the real catalogue (metadata only — never DestinE data)."""

import json
import urllib.parse

import pytest
from conftest import FIXTURES, load_script

dt = load_script("ecmwf-destine", "destine")

LUMI_URL = "https://polytope.lumi.apps.dte.destination-earth.eu/api/v2/"
MN5_URL = "https://polytope.mn5.apps.dte.destination-earth.eu/api/v2/"
CONFIG = json.loads((FIXTURES / "destine-catalogue-config.json").read_text())


def recorded(name):
    return json.loads((FIXTURES / name).read_text())


# --- a model of the catalogue, built from what the live service answers ---------------------


def _days(start, end):
    import datetime as d

    a, b = d.date.fromisoformat(start), d.date.fromisoformat(end)
    return [(a + d.timedelta(n)).strftime("%Y%m%d") for n in range((b - a).days + 1)]


HOURS = [f"{h:02d}00" for h in range(24)]
CLIMATE = {"class": ["d1"], "dataset": ["climate-dt"], "generation": ["2"], "realization": ["1"]}
CLIMATE |= {"expver": ["0001"], "type": ["fc"], "levtype": ["sfc"]}
MONTHLY = {
    **CLIMATE,
    "activity": ["projections"],
    "experiment": ["ssp3-7.0"],
    "model": ["ifs-fesom"],
    "stream": ["clmn"],
    "param": ["228004", "228005", "235055", "235168", "235288"],
    "year": [str(y) for y in range(2015, 2050)],
    "month": [str(m) for m in range(1, 13)],
}
HOURLY_HIST = {
    **CLIMATE,
    "activity": ["baseline"],
    "experiment": ["hist"],
    "stream": ["clte"],
    "param": ["167", "168", "207", "235055"],
    "date": _days("2014-01-01", "2014-01-31") + _days("2014-12-01", "2014-12-31"),
    "time": HOURS,
}
EXTREMES = {
    "class": ["d1"],
    "dataset": ["extremes-dt"],
    "expver": ["0001"],
    "stream": ["oper"],
    "type": ["fc"],
    "levtype": ["sfc"],
    "date": ["20261007", "20261009", "20261010"],
    "time": ["0000"],
    "step": [str(s) for s in range(97)],
    "param": ["167", "228"],
}
# (location, datacube); the live catalogue stores Climate DT hours 0000-0900 as strings and
# 1000-2300 as integers, and a selection that mixes them matches only one of the two groups.
CUBES = [
    ("lumi", {**MONTHLY, "resolution": ["standard"]}),
    ("lumi", {**MONTHLY, "resolution": ["high"]}),
    ("lumi", {**HOURLY_HIST, "model": ["ifs-fesom"], "resolution": ["standard"]}),
    ("mn5", {**HOURLY_HIST, "model": ["ifs-nemo"], "resolution": ["standard"]}),
    (
        "lumi",
        {
            **HOURLY_HIST,
            "experiment": ["cont"],
            "model": ["ifs-fesom"],
            "resolution": ["standard"],
            "date": _days("1990-01-01", "1990-01-31"),
        },
    ),
    ("lumi", EXTREMES),
]
MIXED = {"time"}
INTS = {"date", "year", "month", "param", "generation", "realization", "levelist", "step"}


def _key(v):
    return int(v) if str(v).isdigit() else str(v)


class FakeOmnicat:
    """The four read-only endpoints destine.py calls, answering in the live formats."""

    def __init__(self, cubes=CUBES, urls=None):
        self.cubes = cubes
        self.urls = urls or {"lumi": LUMI_URL, "mn5": MN5_URL}
        self.calls = []

    def __call__(self, url, body=None):
        self.calls.append((url, body))
        path, _, query = url.partition("?")
        if path.endswith("/ai-catalogue/config"):
            return CONFIG
        if path.endswith("/api/v2/axes/"):
            sel = {k: v[0].split("/") for k, v in urllib.parse.parse_qs(query).items()}
            return self.axes(sel)
        if path.endswith("/catalogue/locations"):
            return {"selection": self._echo(body), "locations": self.locations(body)}
        if path.endswith("/catalogue/mars-requests"):
            reqs = [r for loc in self.locations(body) for r in loc["requests"]]
            return {"selection": self._echo(body), "count": len(reqs), "requests": reqs}
        raise AssertionError(f"destine.py called an endpoint it must not use: {url}")

    @staticmethod
    def _echo(body):
        return {
            k: [str(x) for x in (v if isinstance(v, list) else str(v).split("/"))]
            for k, v in body.items()
        }

    def _matched(self, cube, k, wanted):
        have = cube[k]
        wanted = [str(w) for w in wanted]
        if k in MIXED:  # one storage group only: the one holding the first matching value
            groups = [[h for h in have if h < "1000"], [h for h in have if h >= "1000"]]
            first = next((w for w in wanted if w in have), None)
            have = next((g for g in groups if first in g), [])
        hk = {_key(h): h for h in have}
        return [hk[_key(w)] for w in wanted if _key(w) in hk]

    def _match(self, sel):
        out = []
        for loc, cube in self.cubes:
            m = {}
            for k, wanted in sel.items():
                if k not in cube:
                    continue  # the catalogue ignores keys that are not axes of the data
                m[k] = self._matched(cube, k, wanted if isinstance(wanted, list) else [wanted])
                if not m[k]:
                    break
            else:
                out.append((loc, cube, m))
        return out

    def axes(self, sel):
        union = {}
        for _, cube, m in self._match(sel):
            for k, values in cube.items():
                union.setdefault(k, set()).update(m.get(k, values))
        out = {}
        for k, values in sorted(union.items()):
            if k in MIXED:
                ints = sorted(int(v) for v in values if v >= "1000")
                strings = sorted(v for v in values if v < "1000")
                out[k] = json.dumps({"ints": ints, "strings": strings}, separators=(",", ":"))
            else:
                out[k] = sorted((_key(v) if k in INTS else v) for v in values)
        return out

    def locations(self, body):
        sel = {k: (v if isinstance(v, list) else str(v).split("/")) for k, v in body.items()}
        by_loc = {}
        for loc, _, m in self._match(sel):
            req = {k: "/".join(m[k]) for k in sorted(m)}
            by_loc.setdefault(loc, []).append(req)
        return [{"name": n, "url": self.urls.get(n), "requests": r} for n, r in by_loc.items()]


def unreachable(url, body=None):
    raise dt.CatalogueUnavailable("URLError: [Errno -3] Temporary failure in name resolution")


@pytest.fixture
def quiet_status(monkeypatch):
    """ECMWF's status feed is not queried by offline tests."""
    monkeypatch.setattr(dt.ecmwf_status, "_get", lambda url: (_ for _ in ()).throw(OSError("x")))


def run(monkeypatch, capsys, *args, fake=None):
    monkeypatch.setattr(dt, "_catalogue_fetch", fake or FakeOmnicat())
    code = dt.main([*args, "--json"])
    cap = capsys.readouterr()
    return code, json.loads(cap.out), cap.err


MONTHLY_JULY_2040 = (
    "request",
    "climate-dt",
    "--frequency",
    "monthly",
    "--experiment",
    "SSP3-7.0",
    "--param",
    "167",
    "--year",
    "2040",
    "--month",
    "7",
)


# --- the client and the recorded answers -----------------------------------------------------


def test_catalogue_address_can_be_overridden():
    assert dt.catalogue_url({}) == dt.CATALOGUE_URL
    assert dt.CATALOGUE_URL.startswith("https://")
    env = {"DESTINE_CATALOGUE_URL": "https://omnicat.example.test/x/"}
    assert dt.catalogue_url(env) == "https://omnicat.example.test/x"


def test_recorded_locations_answer_is_parsed():
    sent = []

    def fetch(url, body=None):
        sent.append((url, body))
        return recorded("destine-catalogue-locations-july2040.json")

    locs = dt.Catalogue("https://c.test", fetch=fetch).locations({"param": ["228004"]})
    assert sent == [("https://c.test/catalogue/locations", {"param": ["228004"]})]
    assert locs[0]["name"] == "lumi" and locs[0]["url"] == LUMI_URL
    assert locs[0]["requests"][0]["stream"] == "clmn"


def test_recorded_empty_answer_means_no_data():
    cat = dt.Catalogue(
        "https://c.test", fetch=lambda u, b=None: recorded("destine-catalogue-locations-empty.json")
    )
    assert cat.locations({"param": ["167"]}) == []


def test_recorded_mars_requests_answer_counts_matches():
    cat = dt.Catalogue(
        "https://c.test",
        fetch=lambda u, b=None: recorded("destine-catalogue-mars-requests-prefix.json"),
    )
    assert cat.count({"activity": ["projections"]}) == 1


def test_mixed_type_axes_are_decoded_to_one_sorted_list():
    # The live answer encodes `time` as a JSON string {"ints": [...], "strings": [...]}.
    urls = []

    def fetch(url, body=None):
        urls.append(url)
        return recorded("destine-catalogue-axes-time.json")

    axes = dt.Catalogue("https://c.test", fetch=fetch).axes(
        {"date": ["20140101"], "param": ["167"]}
    )
    assert axes["time"] == HOURS
    assert axes["generation"] == ["2"] and axes["param"] == ["167"] and axes["date"] == ["20140101"]
    assert urls == ["https://c.test/api/v2/axes/?date=20140101&param=167"]


def test_recorded_axes_answer_lists_monthly_time_mean_params():
    cat = dt.Catalogue(
        "https://c.test", fetch=lambda u, b=None: recorded("destine-catalogue-axes-clmn.json")
    )
    axes = cat.axes({"stream": ["clmn"]})
    assert "228004" in axes["param"] and "167" not in axes["param"]
    assert axes["year"] == ["2040"] and axes["month"] == ["7"] and axes["generation"] == ["2"]


def test_axes_queries_stay_short():
    # GET query strings have a length limit; long lists (dates) widen the query instead.
    urls = []

    def fetch(url, body=None):
        urls.append(url)
        return {}

    cat = dt.Catalogue("https://c.test", fetch=fetch)
    cat.axes({"stream": ["clte"], "date": _days("1990-01-01", "2014-12-31")})
    assert urls == ["https://c.test/api/v2/axes/?stream=clte"]


def test_selection_expands_mars_ranges():
    # The catalogue matches each listed value; it does not understand to/by.
    sel = dt.selection(
        {
            "date": "20140130/to/20140202",
            "time": "0000/to/0300/by/0100",
            "year": "2040",
            "month": "6/7",
            "feature": {"type": "timeseries"},
        }
    )
    assert sel == {
        "date": ["20140130", "20140131", "20140201", "20140202"],
        "time": ["0000", "0100", "0200", "0300"],
        "year": ["2040"],
        "month": ["6", "7"],
    }
    with pytest.raises(ValueError, match="by"):
        dt.selection({"time": "0000/to/2300"})


def test_location_urls_outside_destine_are_never_trusted():
    assert dt.address_from_url(LUMI_URL) == dt.LUMI
    assert dt.address_from_url(MN5_URL) == dt.MN5
    for bad in (
        "http://polytope.lumi.apps.dte.destination-earth.eu/api/v2/",
        "https://polytope.lumi.apps.dte.destination-earth.eu.evil.test/",
        "https://evil.test/api/v2/",
        None,
    ):
        assert dt.address_from_url(bad) is None, bad


# --- request: validated, normalised, routed by the catalogue ---------------------------------


def test_monthly_mean_is_normalised_mapped_and_routed(monkeypatch, capsys):
    code, d, err = run(monkeypatch, capsys, *MONTHLY_JULY_2040)
    assert code == 0, err
    assert d["validated"] is True and d["errors"] == [] and d["warnings"] == []
    assert d["request"] == {
        "class": "d1",
        "dataset": "climate-dt",
        "activity": "projections",
        "experiment": "ssp3-7.0",
        "generation": "2",
        "model": "ifs-fesom",
        "realization": "1",
        "expver": "0001",
        "stream": "clmn",
        "resolution": "standard",
        "type": "fc",
        "levtype": "sfc",
        "param": "228004",
        "month": "7",
        "year": "2040",
    }
    assert d["location"] == "lumi" and d["address"] == dt.LUMI
    assert d["requests"] == [
        {"location": "lumi", "url": LUMI_URL, "address": dt.LUMI, "request": d["request"]}
    ]
    notes = "\n".join(d["notes"])
    assert "SSP3-7.0 -> ssp3-7.0" in notes
    assert "167 (2 metre temperature) -> 228004 avg_2t (Time-mean 2 metre temperature)" in notes


def test_monthly_text_output_shows_notes_location_and_request(monkeypatch, capsys):
    monkeypatch.setattr(dt, "_catalogue_fetch", FakeOmnicat())
    assert dt.main(list(MONTHLY_JULY_2040)) == 0
    out = capsys.readouterr().out
    assert "validated by the DestinE catalogue" in out and "-> 228004" in out
    assert f"location: lumi  address: {dt.LUMI}" in out and '"stream": "clmn"' in out
    assert "based on data of the European Union" in out


@pytest.mark.parametrize(
    "param, expected",
    [
        ("207", "228005"),  # two "Time-mean 10 metre wind speed" codes; only 228005 exists here
        ("228164", "235288"),  # "Total Cloud Cover" -> "Time-mean total cloud cover"
        ("avg_2t", "228004"),  # short names resolve through the catalogue's labels
        ("228004", "228004"),  # already a time-mean parameter
    ],
)
def test_monthly_params_map_to_time_means(monkeypatch, capsys, param, expected):
    args = list(MONTHLY_JULY_2040)
    args[args.index("167")] = param
    code, d, err = run(monkeypatch, capsys, *args)
    assert code == 0, err
    assert d["request"]["param"] == expected


def test_unavailable_param_is_named_with_the_valid_values(monkeypatch, capsys):
    args = list(MONTHLY_JULY_2040)
    args[args.index("167")] = "3"  # potential temperature: no monthly mean in this selection
    code, d, err = run(monkeypatch, capsys, *args)
    assert code == 2 and d["validated"] is True and d["requests"] == []
    [e] = d["errors"]
    assert e["axis"] == "param" and e["values"] == ["3"] and e["count"] == 5
    assert {"value": "228004", "name": "avg_2t", "label": "Time-mean 2 metre temperature"} in e[
        "options"
    ]
    assert "param 3 (Potential temperature) is not available" in e["message"]
    assert "param 3" in err and "228004  avg_2t  Time-mean 2 metre temperature" in err


def test_a_year_outside_the_run_is_named_after_the_param_is_mapped(monkeypatch, capsys):
    # param comes before year in the catalogue's order: the time-mean mapping must not depend
    # on the year, or param 167 would be blamed for a year the run does not reach.
    args = list(MONTHLY_JULY_2040)
    args[args.index("2040")] = "2060"
    code, d, _ = run(monkeypatch, capsys, *args)
    assert code == 2 and d["request"]["param"] == "228004"
    [e] = d["errors"]
    assert e["axis"] == "year" and e["values"] == ["2060"] and e["ranges"] == ["2015-2049"]


def test_the_first_offending_axis_in_catalogue_order_is_named(monkeypatch, capsys):
    code, d, _ = run(
        monkeypatch,
        capsys,
        "request",
        "climate-dt",
        "--activity",
        "baseline",
        "--experiment",
        "ssp3-7.0",
        "--start",
        "2014-01-01",
        "--end",
        "2014-01-01",
    )
    assert code == 2
    [e] = d["errors"]
    assert e["axis"] == "experiment" and e["values"] == ["ssp3-7.0"]
    assert e["options"] == [
        {"value": "cont", "name": "", "label": "Control"},
        {"value": "hist", "name": "", "label": "Historical"},
    ]


def test_dates_outside_the_run_are_reported_with_the_available_ranges(monkeypatch, capsys):
    code, d, _ = run(
        monkeypatch,
        capsys,
        "request",
        "climate-dt",
        "--model",
        "ifs-nemo",
        "--start",
        "2014-12-31",
        "--end",
        "2015-01-01",
    )
    assert code == 2
    [e] = d["errors"]
    assert e["axis"] == "date" and e["values"] == ["20150101"]
    assert e["ranges"] == ["20140101-20140131", "20141201-20141231"]


def test_all_hours_survive_the_mixed_type_time_axis(monkeypatch, capsys):
    fake = FakeOmnicat()
    code, d, err = run(
        monkeypatch,
        capsys,
        "request",
        "climate-dt",
        "--model",
        "ifs-nemo",
        "--start",
        "2014-01-01",
        "--end",
        "2014-01-02",
        fake=fake,
    )
    assert code == 0, err
    assert d["location"] == "mn5" and d["address"] == dt.MN5
    assert len(d["requests"]) == 1
    assert d["request"]["time"] == "/".join(HOURS)
    assert d["request"]["date"] == "20140101/20140102"
    assert d["request"]["resolution"] == "standard" and d["request"]["stream"] == "clte"
    located = [b for u, b in fake.calls if u.endswith("/catalogue/locations")]
    assert located[-1]["time"] == HOURS[10:]  # the hours the first answer dropped, asked again


def test_extremes_dt_takes_the_latest_catalogue_date(monkeypatch, capsys):
    code, d, err = run(monkeypatch, capsys, "request", "extremes-dt", "--param", "2t")
    assert code == 0, err
    r = d["request"]
    assert r["dataset"] == "extremes-dt" and r["date"] == "20261010" and r["param"] == "167"
    assert r["step"] == "0" and d["address"] == dt.LUMI
    notes = "\n".join(d["notes"])
    assert "20261010" in notes and "2t -> 167" in notes


def test_extremes_dt_point_series_keeps_its_feature(monkeypatch, capsys):
    code, d, err = run(
        monkeypatch, capsys, "request", "extremes-dt", "--lat", "38.72", "--lon", "-9.14"
    )
    assert code == 0, err
    f = d["request"]["feature"]
    assert f["points"] == [[38.72, -9.14]] and f["range"] == {"start": 0, "end": 96}
    assert f["time_axis"] == "step" and "step" not in d["request"]


def test_feature_is_never_sent_to_the_catalogue(monkeypatch, capsys):
    fake = FakeOmnicat()
    run(
        monkeypatch, capsys, "request", "extremes-dt", "--lat", "38.72", "--lon", "-9.14", fake=fake
    )
    assert fake.calls and all("feature" not in (b or {}) for _, b in fake.calls)
    assert all("feature" not in u for u, _ in fake.calls)


def test_untrusted_location_url_falls_back_and_warns(monkeypatch, capsys):
    fake = FakeOmnicat(urls={"lumi": "https://evil.test/api/v2/"})
    code, d, err = run(monkeypatch, capsys, *MONTHLY_JULY_2040, fake=fake)
    assert code == 0 and d["address"] == dt.LUMI
    assert "evil.test" in "\n".join(d["warnings"]) and "evil.test" in err


def test_unreachable_catalogue_lints_offline_and_says_not_validated(
    monkeypatch, capsys, quiet_status
):
    code, d, err = run(monkeypatch, capsys, *MONTHLY_JULY_2040, fake=unreachable)
    assert code == 0
    assert d["validated"] is False and d["errors"] == []
    assert "NOT validated" in err and "NOT validated" in "\n".join(d["warnings"])
    assert d["request"]["stream"] == "clmn" and d["request"]["experiment"] == "SSP3-7.0"
    assert d["address"] == dt.LUMI


def test_unreachable_catalogue_keeps_local_errors(monkeypatch, capsys, quiet_status):
    code, d, _ = run(
        monkeypatch,
        capsys,
        "request",
        "climate-dt",
        "--frequency",
        "monthly",
        "--lat",
        "38.7",
        "--lon",
        "-9.1",
        fake=unreachable,
    )
    assert code == 2 and d["validated"] is False and d["errors"]


# --- options: what exists, with labels ---------------------------------------------------------


def test_options_lists_values_with_labels_and_count(monkeypatch, capsys):
    code, d, err = run(
        monkeypatch,
        capsys,
        "options",
        "climate-dt",
        "--axis",
        "param",
        "--frequency",
        "monthly",
        "--experiment",
        "SSP3-7.0",
        "--model",
        "IFS-FESOM",
    )
    assert code == 0, err
    assert d["axis"] == "param" and d["count"] == 5
    assert d["selection"]["stream"] == ["clmn"] and d["selection"]["experiment"] == ["ssp3-7.0"]
    assert d["selection"]["generation"] == ["2"]
    assert {"value": "228004", "name": "avg_2t", "label": "Time-mean 2 metre temperature"} in d[
        "values"
    ]
    assert "IFS-FESOM -> ifs-fesom" in "\n".join(d["notes"])
    # the listed values span every value of the axes still open: say which, to narrow down
    assert d["also_varies"]["resolution"] == ["high", "standard"]
    assert d["also_varies"]["year"] == ["2015-2049"] and "levtype" not in d["also_varies"]


def test_options_text_lists_one_value_per_line(monkeypatch, capsys):
    monkeypatch.setattr(dt, "_catalogue_fetch", FakeOmnicat())
    args = ["options", "climate-dt", "--axis", "param", "--stream", "clmn"]
    assert dt.main(args) == 0
    out = capsys.readouterr().out
    assert "param: 5 values" in out and "228004  avg_2t  Time-mean 2 metre temperature" in out
    assert "also varies (narrow with --<axis>): resolution high/standard;" in out


def test_options_summarises_dates_as_ranges(monkeypatch, capsys):
    code, d, _ = run(
        monkeypatch, capsys, "options", "climate-dt", "--axis", "date", "--model", "ifs-nemo"
    )
    assert code == 0
    assert d["count"] == 62 and d["ranges"] == ["20140101-20140131", "20141201-20141231"]
    assert "values" not in d


def test_options_names_a_filter_that_matches_nothing(monkeypatch, capsys):
    code, d, err = run(
        monkeypatch,
        capsys,
        "options",
        "climate-dt",
        "--axis",
        "param",
        "--activity",
        "baseline",
        "--experiment",
        "ssp3-7.0",
    )
    assert code == 2
    [e] = d["errors"]
    assert e["axis"] == "experiment" and [o["value"] for o in e["options"]] == ["cont", "hist"]
    assert "error: experiment ssp3-7.0" in err and "hist  Historical" in err


def test_options_for_an_axis_the_data_does_not_have(monkeypatch, capsys):
    code, d, _ = run(
        monkeypatch, capsys, "options", "climate-dt", "--axis", "date", "--frequency", "monthly"
    )
    assert code == 2
    assert "date" in d["errors"][0]["message"] and "year" in d["errors"][0]["message"]


def test_options_without_the_catalogue_is_blocked(monkeypatch, capsys, quiet_status):
    monkeypatch.setattr(dt, "_catalogue_fetch", unreachable)
    assert dt.main(["options", "climate-dt", "--axis", "param"]) == 2
    err = capsys.readouterr().err
    assert "BLOCKED: cannot reach the ECMWF destine-catalogue service" in err


# --- live: the real catalogue (metadata only) ------------------------------------------------


@pytest.mark.live
def test_live_monthly_mean_july_2040(capsys):
    code = dt.main([*MONTHLY_JULY_2040, "--json"])
    d = json.loads(capsys.readouterr().out)
    assert code == 0 and d["validated"] is True, d["errors"]
    r = d["request"]
    assert (r["stream"], r["param"], r["experiment"], r["resolution"]) == (
        "clmn",
        "228004",
        "ssp3-7.0",
        "standard",
    )
    assert (r["year"], r["month"], r["generation"], r["class"]) == ("2040", "7", "2", "d1")
    assert d["location"] == "lumi" and d["address"] == dt.LUMI and len(d["requests"]) == 1


@pytest.mark.live
def test_live_extremes_dt_request(capsys):
    code = dt.main(["request", "extremes-dt", "--param", "167", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert code == 0 and d["validated"] is True, d["errors"]
    assert len(d["request"]["date"]) == 8 and d["request"]["dataset"] == "extremes-dt"
    assert d["address"].endswith(".apps.dte.destination-earth.eu")


@pytest.mark.live
def test_live_hourly_request_keeps_every_hour(capsys):
    args = ["request", "climate-dt", "--model", "ifs-nemo", "--start", "2014-01-01"]
    code = dt.main([*args, "--end", "2014-01-01", "--json"])
    d = json.loads(capsys.readouterr().out)
    assert code == 0, d["errors"]
    assert d["request"]["time"] == "/".join(HOURS) and len(d["requests"]) == 1


@pytest.mark.live
def test_live_invalid_param_is_named_with_options(capsys):
    args = list(MONTHLY_JULY_2040)
    args[args.index("167")] = "3"
    assert dt.main([*args, "--json"]) == 2
    [e] = json.loads(capsys.readouterr().out)["errors"]
    assert e["axis"] == "param" and "228004" in [o["value"] for o in e["options"]]


@pytest.mark.live
def test_live_options_monthly_parameters(capsys):
    args = ["options", "climate-dt", "--axis", "param", "--frequency", "monthly"]
    assert dt.main([*args, "--experiment", "ssp3-7.0", "--model", "ifs-fesom", "--json"]) == 0
    d = json.loads(capsys.readouterr().out)
    labels = {v["value"]: v["label"] for v in d["values"]}
    assert labels["228004"] == "Time-mean 2 metre temperature" and d["count"] == len(labels)
