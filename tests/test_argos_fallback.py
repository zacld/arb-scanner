from pathlib import Path
from unittest.mock import MagicMock

import pytest

from arbfinder.sources.argos import ArgosScraper, ScrapeBlocked

DEMO_HTML = (
    Path(__file__).parent.parent / "arbfinder" / "demo_data" / "argos_search.html"
).read_text()


def _session(status=403, text=""):
    session = MagicMock()
    session.min_delay = 0
    session.jitter = 0
    session.allowed.return_value = True
    resp = MagicMock()
    resp.status_code = status
    resp.text = text
    session.get.return_value = resp
    return session


def _browser(html):
    browser = MagicMock()
    browser.fetch.return_value = html
    browser.unavailable_reason = None if html else "Playwright is not installed"
    return browser


def test_403_falls_back_to_browser_and_parses():
    browser = _browser(DEMO_HTML)
    products = ArgosScraper(_session(status=403), browser=browser).scrape("https://x/search/y/")
    assert len(products) == 6
    browser.fetch.assert_called_once()
    browser.close.assert_called()


def test_empty_200_page_also_falls_back():
    browser = _browser(DEMO_HTML)
    session = _session(status=200, text="<html><body>pretty shell, no data</body></html>")
    products = ArgosScraper(session, browser=browser).scrape("https://x/search/y/")
    assert len(products) == 6


def test_blocked_everywhere_raises_scrapeblocked_with_hint():
    with pytest.raises(ScrapeBlocked) as exc:
        ArgosScraper(_session(status=403), browser=_browser(None)).scrape("https://x/search/y/")
    msg = str(exc.value)
    assert "Playwright is not installed" in msg
    assert "--show-browser" in msg


def test_200_with_products_never_touches_browser():
    browser = _browser(DEMO_HTML)
    session = _session(status=200, text=DEMO_HTML)
    products = ArgosScraper(session, browser=browser).scrape("https://x/search/y/")
    assert len(products) == 6
    browser.fetch.assert_not_called()


def test_after_fallback_detail_pages_use_browser_too():
    browser = _browser(DEMO_HTML)
    scraper = ArgosScraper(_session(status=403), browser=browser)
    scraper.scrape("https://x/search/y/", max_products=2, fetch_ean=True)
    # 1 search page + 1 detail page (the other product already has an EAN
    # from the embedded JSON); plain HTTP is never retried once refused.
    assert browser.fetch.call_count == 2
