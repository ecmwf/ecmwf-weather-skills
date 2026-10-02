import sys

from conftest import ROOT

sys.path.insert(0, str(ROOT / "scripts"))
import check_links  # noqa: E402


def test_extract_urls_skips_templates_and_placeholders():
    text = """See https://www.ecmwf.int/en/terms-use and <https://example.org/x>.
    Template https://data.ecmwf.int/forecasts/{date}/x and https://eccharts.ecmwf.int/wms/?token=public&request=GetMap&layers=…
    Placeholder https://polytope.lumi.apps.dte.destination-earth.eu/` and `https://api.ecmwf.int/v1/key/)."""
    urls = check_links.extract_urls(text)
    assert "https://www.ecmwf.int/en/terms-use" in urls
    assert "https://example.org/x" in urls
    assert "https://api.ecmwf.int/v1/key/" in urls
    assert not any("{" in u or "…" in u for u in urls)


def test_all_skill_urls_collected():
    urls = check_links.collect(ROOT / "plugins" / "ecmwf-weather" / "skills")
    assert any("creativecommons.org/licenses/by/4.0" in u for u in urls)
