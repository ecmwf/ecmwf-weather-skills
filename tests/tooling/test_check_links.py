# SPDX-FileCopyrightText: 2026 European Centre for Medium-Range Weather Forecasts (ECMWF)
#
# SPDX-License-Identifier: Apache-2.0

import check_links
import pytest
from conftest import ROOT


def test_extract_urls_skips_templates_and_placeholders():
    text = """See https://www.ecmwf.int/en/terms-use and <https://example.org/x>.
    Template https://data.ecmwf.int/forecasts/{date}/x and https://eccharts.ecmwf.int/wms/?token=public&request=GetMap&layers=…
    Placeholder https://polytope.lumi.apps.dte.destination-earth.eu/` and `https://api.ecmwf.int/v1/key/)."""
    urls = check_links.extract_urls(text)
    assert "https://www.ecmwf.int/en/terms-use" in urls
    assert "https://example.org/x" in urls
    assert "https://api.ecmwf.int/v1/key/" in urls
    assert not any("{" in u or "…" in u for u in urls)


def test_extract_urls_skips_urls_cut_short_by_a_placeholder_or_an_ellipsis():
    text = """Template `https://github.com/ecmwf/<package>/issues/new/choose` and
    prefilled https://github.com/ecmwf/earthkit-data/issues/new?title=...&body=...
    but a real link https://github.com/ecmwf/earthkit-data/issues/new/choose stays."""
    urls = check_links.extract_urls(text)
    assert urls == ["https://github.com/ecmwf/earthkit-data/issues/new/choose"]


@pytest.mark.parametrize(
    "text",
    [
        "https://example.org/path?body=...",
        "https://example.org/a...",
        "(https://example.org/a...).",
        "see https://example.org/x?title=...&body=....",
        "https://example.org/path?body=…",
        "https://example.org/a…).",
        "see https://example.org/x?title=…&body=….",
    ],
)
def test_extract_urls_skips_truncated_examples_whatever_follows_the_ellipsis(text):
    assert check_links.extract_urls(text) == []


def test_extract_urls_strips_sentence_punctuation_from_ordinary_urls():
    assert check_links.extract_urls("see https://example.org/page.") == ["https://example.org/page"]
    assert check_links.extract_urls("(https://example.org/a, https://example.org/b;)") == [
        "https://example.org/a",
        "https://example.org/b",
    ]


def test_all_skill_urls_collected():
    urls = check_links.collect(ROOT / "plugins" / "ecmwf-weather" / "skills")
    assert any("creativecommons.org/licenses/by/4.0" in u for u in urls)


def test_skills_hold_real_examples_not_url_templates():
    urls = check_links.collect(ROOT / "plugins" / "ecmwf-weather" / "skills")
    assert not [u for u in urls if "earthkit-X" in u], "use a real repository in examples"
    assert not [u for u in urls if u.endswith("github.com/ecmwf/")], "template cut short"
    assert "https://github.com/ecmwf/earthkit-data/issues/new/choose" in urls


def test_service_endpoints_are_left_to_check_endpoints():
    import check_endpoints

    assert set(check_links.SERVICE_ENDPOINTS) == {
        "https://charts.mcp.ecmwf.int/mcp",
        "https://catalogue-test.lumi.apps.dte.destination-earth.eu/mcp",
        "https://aviationweather.gov/api/data",
    }
    probed = check_endpoints.probed_urls()
    for url, reason in check_links.SERVICE_ENDPOINTS.items():
        assert "check_endpoints" in reason
        assert any(p == url or p.startswith(f"{url}/") for p in probed), url
        assert check_links.extract_urls(f"see {url} for the API") == []


def test_no_skill_url_is_a_service_endpoint():
    urls = check_links.collect(ROOT / "plugins" / "ecmwf-weather" / "skills")
    assert not set(urls) & set(check_links.SERVICE_ENDPOINTS)
