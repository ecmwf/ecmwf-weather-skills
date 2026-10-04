# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import json
import subprocess
import sys
from datetime import date, datetime, timezone

import pytest
from conftest import SKILLS, load_script

od = load_script("ecmwf-open-data", "odcatalog")
UTC = timezone.utc


# --- run schedule -----------------------------------------------------------------------------


def test_ifs_00z_steps_3h_to_144_then_6h_to_360():
    steps = od.steps_for("ifs", "oper", 0)
    assert steps[:3] == [0, 3, 6]
    assert 144 in steps and 147 not in steps and 150 in steps
    assert steps[-1] == 360


def test_ifs_06z_post_50r1_goes_to_144_in_oper():
    steps = od.steps_for("ifs", "oper", 6, run_date=date(2026, 10, 1))
    assert steps[-1] == 144 and steps[1] == 3
    assert od.stream_for("ifs", "oper", 6, date(2026, 10, 1)) == "oper"


def test_ifs_06z_pre_50r1_uses_scda_to_90():
    assert od.stream_for("ifs", "oper", 18, date(2026, 5, 1)) == "scda"
    assert od.stream_for("ifs", "wave", 6, date(2026, 5, 1)) == "scwv"
    assert od.steps_for("ifs", "oper", 6, run_date=date(2026, 5, 1))[-1] == 90


def test_aifs_single_6h_to_360_every_run():
    for hour in (0, 6, 12, 18):
        steps = od.steps_for("aifs-single", "oper", hour)
        assert steps[0] == 0 and steps[1] == 6 and steps[-1] == 360


def test_unknown_model_rejected():
    with pytest.raises(ValueError):
        od.steps_for("gfs", "oper", 0)


# --- URLs ---------------------------------------------------------------------------------------


def test_file_url_ifs_oper():
    run = datetime(2026, 10, 1, 0, tzinfo=UTC)
    assert od.file_url(run, "ifs", "oper", 24) == (
        "https://data.ecmwf.int/forecasts/20261001/00z/ifs/0p25/oper/"
        "20261001000000-24h-oper-fc.grib2"
    )


def test_file_url_index_and_mirror_and_types():
    run = datetime(2026, 10, 1, 12, tzinfo=UTC)
    url = od.file_url(run, "ifs", "enfo", 6, source="aws", ext="index")
    assert url == (
        "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com/20261001/12z/ifs/0p25/enfo/"
        "20261001120000-6h-enfo-ef.index"
    )
    assert od.file_url(run, "aifs-ens", "enfo", 6).endswith("-6h-enfo-pf.grib2")
    assert "/aifs-single/0p25/oper/" in od.file_url(run, "aifs-single", "oper", 6)


def test_file_url_pre_50r1_06z_rewrites_stream():
    run = datetime(2026, 5, 1, 6, tzinfo=UTC)
    assert "/06z/ifs/0p25/scda/20260501060000-3h-scda-fc.grib2" in od.file_url(
        run, "ifs", "oper", 3
    )


# --- index files ------------------------------------------------------------------------------


def test_parse_index_and_select(fixtures):
    entries = od.parse_index((fixtures / "ifs-oper-24h.index").read_text())
    assert entries and isinstance(entries[0]["_offset"], int)
    sel = od.select(entries, params=["2t", "tp"])
    assert {e["param"] for e in sel} == {"2t", "tp"}
    assert all(e["levtype"] == "sfc" for e in sel)


def test_select_pressure_levels(fixtures):
    entries = od.parse_index((fixtures / "ifs-oper-24h.index").read_text())
    sel = od.select(entries, params=["t"], levtype="pl", levelist=[850, 500])
    assert sorted(int(e["levelist"]) for e in sel) == [500, 850]


def test_summarise_fields_separates_pressure_and_soil_levels(fixtures):
    entries = od.parse_index((fixtures / "ifs-oper-24h.index").read_text())
    s = od.summarise_fields(entries)
    assert "2t" in s["params"]["sfc"]
    assert 850 in s["pressure_levels"] and 1 not in s["pressure_levels"]
    assert s["soil_levels"] and max(s["soil_levels"]) < 10


def test_estimate_bytes_and_merged_ranges(fixtures):
    entries = od.parse_index((fixtures / "ifs-oper-24h.index").read_text())
    first_two = sorted(entries, key=lambda e: e["_offset"])[:2]  # contiguous
    assert od.estimate_bytes(first_two) == sum(e["_length"] for e in first_two)
    ranges = od.byte_ranges(first_two)
    assert ranges == [
        (first_two[0]["_offset"], first_two[1]["_offset"] + first_two[1]["_length"] - 1)
    ]


def test_parallel_download_preserves_order_and_uses_workers(tmp_path):
    import threading

    seen = set()

    def fake_fetch(url, start, end):
        seen.add(threading.get_ident())
        return f"{url}:{start}-{end};".encode()

    jobs = [("u1", 0, 9), ("u1", 20, 29), ("u2", 0, 4), ("u3", 5, 9)]
    out = tmp_path / "x.grib2"
    n = od.download_ranges(jobs, out, workers=4, fetch=fake_fetch)
    assert out.read_bytes() == b"u1:0-9;u1:20-29;u2:0-4;u3:5-9;"
    assert n == len(out.read_bytes())


def test_parallel_download_caps_workers():
    assert od.clamp_workers(100) == 16 and od.clamp_workers(0) == 1


def test_human_size():
    assert od.human_size(1536) == "1.5 KB"
    assert od.human_size(147 * 1024 * 1024) == "147.0 MB"


# --- listings & latest run --------------------------------------------------------------------


def test_available_dates_from_listing(fixtures):
    dates = od.available_dates((fixtures / "listing-root.html").read_text())
    assert dates == ["20260929", "20260930", "20261001", "20261002"]


def test_steps_from_listing(fixtures):
    html = (fixtures / "listing-ifs-06z-oper.html").read_text()
    assert od.steps_in_listing(html, stream="oper", type_="fc")[-1] == 144


def test_latest_run_walks_back_until_last_step_exists():
    now = datetime(2026, 10, 2, 16, 30, tzinfo=UTC)
    published = {"20261002060000-144h-oper-fc"}  # 12z not complete yet, 06z complete

    def exists(url):
        return any(p in url for p in published)

    run = od.latest_run("ifs", "oper", now=now, exists=exists)
    assert run == datetime(2026, 10, 2, 6, tzinfo=UTC)


def test_latest_run_with_explicit_step_accepts_partial_run():
    now = datetime(2026, 10, 2, 16, 30, tzinfo=UTC)
    run = od.latest_run(
        "ifs", "oper", now=now, step=0, exists=lambda url: "20261002120000-0h" in url
    )
    assert run == datetime(2026, 10, 2, 12, tzinfo=UTC)


def test_latest_run_none_when_nothing_found():
    now = datetime(2026, 10, 2, 16, tzinfo=UTC)
    assert od.latest_run("ifs", "oper", now=now, exists=lambda u: False) is None


def test_step_range_beyond_run_length_is_an_error_not_truncated():
    with pytest.raises(ValueError, match="144"):
        od._parse_steps("0-240", od.steps_for("ifs", "oper", 6))


def test_latest_run_for_long_range_skips_short_06z_run():
    now = datetime(2026, 10, 2, 16, 30, tzinfo=UTC)
    published = {"20261002060000-0h", "20261002000000-240h"}
    run = od.latest_run(
        "ifs",
        "oper",
        now=now,
        step=240,
        exists=lambda url: any(p in url for p in published),
    )
    assert run == datetime(2026, 10, 2, 0, tzinfo=UTC)


def test_max_step_in_spec():
    assert od.max_step("0-240") == 240
    assert od.max_step("24,6") == 24


# --- freshness & attribution -----------------------------------------------------------------


def test_freshness():
    run = datetime(2026, 10, 2, 6, tzinfo=UTC)
    f = od.freshness(run, "ifs", now=datetime(2026, 10, 2, 16, tzinfo=UTC))
    assert f["run"] == "2026-10-02T06:00Z"
    assert f["age_hours"] == 10.0
    assert f["next_run"] == "2026-10-02T12:00Z"
    assert "approx" in f["next_run_available"]


def test_attribution_contains_required_parts():
    a = od.attribution(2026)
    assert a["licence"] == "CC-BY-4.0"
    assert "© 2026 ECMWF" in a["short"]
    full = a["full"]
    assert "European Centre for Medium-Range Weather Forecasts (ECMWF)" in full
    assert "creativecommons.org/licenses/by/4.0" in full
    assert "does not accept any liability" in full


# --- CLI ----------------------------------------------------------------------------------------


def run_cli(*args):
    script = SKILLS / "ecmwf-open-data" / "scripts" / "odcatalog.py"
    out = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)
    return out


def test_cli_url_offline_json():
    out = run_cli(
        "url",
        "--date",
        "20261001",
        "--time",
        "0",
        "--model",
        "ifs",
        "--stream",
        "oper",
        "--step",
        "24",
        "--json",
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["grib2"].endswith("20261001000000-24h-oper-fc.grib2")
    assert data["index"].endswith(".index")
    assert data["attribution"]["licence"] == "CC-BY-4.0"


def test_cli_steps_offline():
    out = run_cli("steps", "--model", "aifs-single", "--time", "12", "--json")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["steps"][-1] == 360


def test_cli_rejects_bad_model():
    out = run_cli("steps", "--model", "gfs", "--time", "0")
    assert out.returncode != 0
    assert "model" in out.stderr.lower()


# --- live ---------------------------------------------------------------------------------------


@pytest.mark.live
def test_live_latest_and_estimate():
    out = run_cli("latest", "--model", "ifs", "--json")
    assert out.returncode == 0, out.stderr
    run = json.loads(out.stdout)["run"]
    d, t = run[:10].replace("-", ""), run[11:13]
    out = run_cli(
        "estimate",
        "--date",
        d,
        "--time",
        t,
        "--step",
        "24",
        "--param",
        "2t,tp",
        "--json",
    )
    assert out.returncode == 0, out.stderr
    data = json.loads(out.stdout)
    assert data["fields"] == 2 and data["bytes"] > 100_000


def test_no_run_barrier():
    b = od.no_run_barrier("ecmwf", exists=lambda url: True)  # server reachable, no run
    assert "--source aws" in "\n".join(b["user_steps"]) + b["agent"]


def test_download_ranges_cache_avoids_refetching(tmp_path):
    calls = []

    def fetch(url, start, end):
        calls.append((url, start, end))
        return f"{url}:{start}-{end};".encode()

    jobs = [("u1", 0, 9), ("u2", 0, 4)]
    cache = tmp_path / "cache"
    od.download_ranges(jobs, tmp_path / "a.grib2", fetch=fetch, cache=cache)
    od.download_ranges(jobs, tmp_path / "b.grib2", fetch=fetch, cache=cache)
    assert len(calls) == 2
    assert (tmp_path / "a.grib2").read_bytes() == (tmp_path / "b.grib2").read_bytes()


def test_cache_dir_from_environment(tmp_path):
    assert od.cache_dir(env={}) is None
    assert od.cache_dir(env={"ECMWF_SKILLS_CACHE": str(tmp_path)}) == tmp_path / "open-data"


def test_cache_prunes_old_entries(tmp_path):
    import os
    import time

    d = tmp_path / "open-data"
    d.mkdir()
    old, new = d / "old", d / "new"
    old.write_bytes(b"x")
    new.write_bytes(b"y")
    os.utime(old, (time.time() - 5 * 86400,) * 2)
    od.prune_cache(d)
    assert not old.exists() and new.exists()
