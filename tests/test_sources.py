from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arbfinder.sources import johnlewis
from arbfinder.sources.base import (
    SOURCES,
    BrowserBackedScraper,
    ScrapeBlocked,
    make_scraper,
    resolve_target,
)

JL_HTML = (Path(__file__).parent / "fixtures" / "johnlewis_search.html").read_text()
ARGOS_FLIGHT = (
    Path(__file__).parent / "fixtures" / "argos_flight_search.html"
).read_text()


def test_registry_has_argos_and_johnlewis():
    assert set(SOURCES) == {"argos", "johnlewis"}
    assert SOURCES["argos"].verified is True
    assert SOURCES["johnlewis"].verified is False  # beta until confirmed live


def test_search_url_builders():
    assert resolve_target("argos", "air fryer") == "https://www.argos.co.uk/search/air%20fryer/"
    assert resolve_target("johnlewis", "air fryer") == (
        "https://www.johnlewis.com/search?search-term=air+fryer"
    )


def test_full_url_passes_through_unchanged():
    url = "https://www.argos.co.uk/search/kettle/"
    assert resolve_target("argos", url) == url


def test_johnlewis_parses_ldjson():
    products = johnlewis.parse_search_page(JL_HTML)
    assert len(products) == 2
    ninja = products[0]
    assert ninja.name.startswith("Ninja Foodi MAX Dual Zone AF400UK")
    assert ninja.price == 199.99
    assert ninja.ean == "0622356254529"
    assert ninja.source == "johnlewis"
    assert ninja.url == "https://www.johnlewis.com/p/ninja-foodi-af400uk/p5432109"


def _scraper_with_html(source_name, html, status=200):
    session = MagicMock()
    session.min_delay = 0
    session.jitter = 0
    session.allowed.return_value = True
    resp = MagicMock()
    resp.status_code = status
    resp.text = html
    session.get.return_value = resp
    return make_scraper(source_name, session=session, browser=MagicMock())


def test_scraper_tags_products_with_source_and_limits():
    scraper = _scraper_with_html("argos", ARGOS_FLIGHT)
    products = scraper.scrape("air fryer", max_products=2)
    assert len(products) == 2
    assert all(p.source == "argos" for p in products)


def test_scraper_raises_with_beta_hint_for_johnlewis():
    scraper = _scraper_with_html("johnlewis", "<html><body>nothing here</body></html>")
    scraper.browser.fetch.return_value = "<html><body>still nothing</body></html>"
    with pytest.raises(ScrapeBlocked) as exc:
        scraper.scrape("air fryer")
    assert "John Lewis" in str(exc.value)
    assert "not yet confirmed" in str(exc.value)
