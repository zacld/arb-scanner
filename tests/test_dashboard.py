import pytest

flask = pytest.importorskip("flask")

from arbfinder import config, dashboard
from arbfinder.demo import DemoEbayClient, demo_products
from arbfinder.pipeline import compare_products
from arbfinder.report import sort_comparisons


@pytest.fixture(autouse=True)
def isolate_credentials(tmp_path, monkeypatch):
    # Never touch the real ~/.arbfinder-credentials.json during tests.
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "creds.json")
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)
    # Isolate the job store / results / hosted flags per test.
    monkeypatch.setattr(dashboard, "DATA_DIR", tmp_path)
    monkeypatch.setattr(dashboard, "RESULTS_PATH", tmp_path / "results.csv")
    monkeypatch.setattr(dashboard, "_STORE", None)
    monkeypatch.setattr(dashboard, "HOSTED", False)
    monkeypatch.delenv("ARBFINDER_PASSWORD", raising=False)


@pytest.fixture
def client():
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_index_renders_form(client):
    body = client.get("/").get_data(as_text=True)
    assert "retail-arbitrage-finder" in body
    assert 'name="url"' in body
    assert 'type="password"' in body  # secret field is masked input


def test_results_table_from_real_comparisons():
    comps = sort_comparisons(compare_products(demo_products(), DemoEbayClient()),
                             fees_pct=13, postage=0)
    out = dashboard._results_table(comps, "ebay", 13.0, 0.0)
    assert out.count("<tr>") == len(comps) + 1  # + header row
    assert "Tommee Tippee" in out
    assert 'class="pos"' in out and 'class="neg"' in out  # gains and losses coloured


def test_secret_is_never_echoed_back():
    page = dashboard._render(
        results="", form={"ebay_secret": "TOPSECRET123", "comparator": "ebay", "url": "u"}
    )
    assert "TOPSECRET123" not in page


def test_saved_credentials_prefill_id_not_secret():
    config.save_credentials("MY-APP-ID", "MY-CERT-SECRET")
    page = dashboard._render()
    assert "MY-APP-ID" in page          # Client ID pre-filled for convenience
    assert "MY-CERT-SECRET" not in page  # secret NEVER rendered into the page
    assert "saved secret will be used" in page


def test_remember_saves_and_forget_clears(client):
    # A blank-secret ebay scan with saved creds resolves them (no "needs both").
    config.save_credentials("APPID", "SECRET")
    out = dashboard._run_scan(
        {"url": "https://www.argos.co.uk/search/x/", "comparator": "ebay",
         "ebay_id": "", "ebay_secret": ""}
    )
    assert "eBay needs both" not in out

    # /forget removes them.
    client.get("/forget")
    assert not config.has_saved_secret()


def test_remember_checkbox_persists_typed_credentials():
    dashboard._run_scan(
        {"url": "https://www.argos.co.uk/search/x/", "comparator": "ebay",
         "ebay_id": "TYPED_ID", "ebay_secret": "TYPED_SECRET", "remember": "1"}
    )
    assert config.load_credentials() == {
        "ebay_client_id": "TYPED_ID", "ebay_client_secret": "TYPED_SECRET"
    }


def test_scan_validates_no_input():
    out = dashboard._run_scan({"url": "", "comparator": "google"}, {})
    assert "Enter a search term" in out


def test_min_net_filters_out_unprofitable_rows(monkeypatch):
    # Two ebay comparisons: one clears net, one loses money. min_net=0 keeps
    # only the winner. Stub the scrape + compare so no network/browser is needed.
    from arbfinder.models import Comparison, Product

    class _FakeScraper:
        def scrape(self, query, max_products=None):
            return [Product(name="Winner", price=40.0, url="u", source="argos")]

    winner = Comparison(product=Product(name="Winner", price=40.0, url="u"),
                        market="ebay", market_price=60.0, market_url="m",
                        n_listings=5, matched_by="title")   # net ≈ +12.20 at 13%/£0
    loser = Comparison(product=Product(name="Loser", price=100.0, url="u"),
                       market="ebay", market_price=101.0, market_url="m",
                       n_listings=5, matched_by="title")    # net ≈ -12.13
    monkeypatch.setattr(dashboard, "make_scraper", lambda *a, **k: _FakeScraper())
    monkeypatch.setattr(dashboard, "compare_products", lambda p, c, **k: [winner, loser])
    config.save_credentials("APPID", "SECRET")
    out = dashboard._run_scan(
        {"url": "air fryer", "comparator": "ebay", "source": "argos",
         "fetch_mode": "browser", "min_net": "0"},
    )
    # Only the profitable row survives the filter.
    assert "Winner" in out
    assert "Loser" not in out
    assert "net ≥ £0.00" in out


def test_hunt_mode_discovers_and_hunts(monkeypatch, tmp_path):
    # Ticking "hunt trending" discovers items (stubbed), hunts them at the
    # source (stubbed scraper), and reports — no search term needed.
    from arbfinder import browser as browser_mod, discover as discover_mod
    from arbfinder.discover import Idea
    from arbfinder.models import Comparison, Product

    class _FakeFetcher:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    class _FakeScraper:
        def scrape(self, term, max_products=None):
            return [Product(name=term, price=10.0, url="u", source="argos")]

    from arbfinder.trends import cache as trends_cache
    monkeypatch.setattr(trends_cache, "CACHE_PATH", tmp_path / "trends.json")
    trends_cache._MEMO.clear()

    monkeypatch.setattr(browser_mod, "BrowserFetcher", _FakeFetcher)
    monkeypatch.setattr(discover_mod, "discover_bestsellers",
                        lambda *a, **k: [Idea(term="Ninja Air Fryer", reason="Movers")])
    monkeypatch.setattr(dashboard, "make_scraper", lambda *a, **k: _FakeScraper())
    comp = Comparison(product=Product(name="Ninja Air Fryer", price=10.0, url="u"),
                      market="ebay", market_price=30.0, market_url="m",
                      n_listings=3, matched_by="title")
    monkeypatch.setattr(dashboard, "compare_products", lambda p, c, **k: [comp])
    config.save_credentials("APPID", "SECRET")

    # Hunt with just the manual signal so the result is deterministic.
    out = dashboard._run_scan(
        {"hunt": "1", "comparator": "ebay", "source": "argos", "fetch_mode": "browser",
         "sig_manual": "1", "trend_terms": "air fryer", "ebay_id": "", "ebay_secret": ""})
    assert "Discovered" in out and "categor" in out       # discovery summary shown
    assert "Ninja Air Fryer" in out                        # opportunity row
    assert "opportunity(ies)" in out                       # profit-first table
    assert 'href="/download"' in out                       # CSV export link


def test_hunt_shows_near_misses_when_nothing_clears_gates(monkeypatch, tmp_path):
    # A hunt that prices a product too thin to clear the gates must NOT dead-end:
    # it shows the closest below-threshold rows so you can judge and adjust.
    from arbfinder import browser as browser_mod, discover as discover_mod
    from arbfinder.discover import Idea
    from arbfinder.models import Comparison, Product

    class _FakeFetcher:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    class _FakeScraper:
        def scrape(self, term, max_products=None):
            return [Product(name=term, price=10.0, url="u", source="argos")]

    from arbfinder.trends import cache as trends_cache
    monkeypatch.setattr(trends_cache, "CACHE_PATH", tmp_path / "trends.json")
    trends_cache._MEMO.clear()

    monkeypatch.setattr(browser_mod, "BrowserFetcher", _FakeFetcher)
    monkeypatch.setattr(discover_mod, "discover_bestsellers",
                        lambda *a, **k: [Idea(term="Thin Margin Item", reason="Movers")])
    monkeypatch.setattr(dashboard, "make_scraper", lambda *a, **k: _FakeScraper())
    # buy £10 → sell £12: a real but tiny margin that a £50 net gate excludes.
    comp = Comparison(product=Product(name="Thin Margin Item", price=10.0, url="u"),
                      market="ebay", market_price=12.0, market_url="m",
                      n_listings=3, matched_by="title")
    monkeypatch.setattr(dashboard, "compare_products", lambda p, c, **k: [comp])
    config.save_credentials("APPID", "SECRET")

    out = dashboard._run_scan(
        {"hunt": "1", "comparator": "ebay", "source": "argos", "fetch_mode": "browser",
         "sig_manual": "1", "trend_terms": "thin", "min_net": "50",  # impossible gate
         "ebay_id": "", "ebay_secret": ""})
    assert "closest below-threshold" in out          # the near-miss banner fired
    assert "£50.00 net" in out                        # the active gate is named
    assert "Thin Margin Item" in out                  # the row is still shown
    assert 'href="/download"' in out                  # and downloadable
    assert "cleared the thresholds" not in out        # not the old dead-end message


class _FakeComparator:
    """Duck-types a comparator; returns listings titled like the query."""

    def __init__(self, market, price, n, min_listings=1, *a, **k):
        self.market_name = market
        self.resale_market = True
        self.default_min_listings = min_listings
        self._price, self._n = price, n

    def search(self, query=None, gtin=None, **_):
        from arbfinder.models import ComparableListing
        term = query or gtin or ""
        if self._n <= 0:
            return []
        return [ComparableListing(title=term, price=self._price, shipping=0.0,
                                  url=f"https://{self.market_name}/{i}", condition="",
                                  seller=self.market_name) for i in range(self._n)]

    def close(self):
        pass


def test_mismatch_scan_sweeps_and_prices_on_amazon_and_ebay(monkeypatch):
    # Wide-net mismatch scan: sweep an Argos category (stubbed), price each on
    # Amazon (primary) + eBay (secondary), surface the profitable gap.
    from arbfinder import browser as browser_mod
    from arbfinder.comparators import amazon as amazon_mod, ebay as ebay_mod
    from arbfinder.models import Product

    class _FakeFetcher:
        def __init__(self, *a, **k): pass
        def close(self): pass

    class _FakeScraper:
        def scrape(self, url):
            return [Product(name="Widget", price=10.0,
                            url="https://argos/widget", source="argos")]

    monkeypatch.setattr(browser_mod, "BrowserFetcher", _FakeFetcher)
    monkeypatch.setattr(dashboard, "make_scraper", lambda *a, **k: _FakeScraper())
    monkeypatch.setattr(amazon_mod, "AmazonClient",
                        lambda *a, **k: _FakeComparator("amazon", 30.0, 1))
    monkeypatch.setattr(ebay_mod, "EbayBrowseClient",
                        lambda *a, **k: _FakeComparator("ebay", 28.0, 3, min_listings=3))
    config.save_credentials("APPID", "SECRET")  # so the eBay secondary is built

    out = dashboard._run_scan(
        {"mismatch": "1", "fetch_mode": "browser", "source": "argos",
         "category": "sale", "pages": "1", "show_all": "1",
         "ebay_id": "", "ebay_secret": ""})
    assert "Swept" in out and "Widget" in out          # sweep summary + product row
    assert "Sell on" in out                             # multi-venue column present
    assert "amazon" in out and "ebay" in out            # both venues shown
    assert 'href="/download"' in out                    # CSV export


def test_mismatch_controls_render():
    body = dashboard._render()
    assert 'name="mismatch"' in body
    assert 'name="category"' in body and "Sale / Clearance" in body
    assert 'name="category_url"' in body


def test_profit_gate_defaults_prefilled_and_show_all_present():
    body = dashboard._render()
    # Fresh page pre-fills the profit gates (£5 net / 15% ROI / 80% match).
    assert 'name="min_net" type="number" step="0.01" value="5"' in body
    assert 'name="min_roi" type="number" step="1" value="15"' in body
    assert 'name="min_match" type="number" step="1" value="80"' in body
    assert 'name="show_all"' in body  # editable escape hatch


def test_dashboard_offers_my_chrome_fetch_mode():
    body = dashboard._render()
    assert 'name="fetch_mode"' in body
    assert "My Chrome" in body
    assert 'name="cdp_url"' in body
    assert 'name="min_net"' in body


class _FakeUpload:
    filename = "argos.html"

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def test_uploaded_page_is_parsed(monkeypatch, tmp_path):
    from pathlib import Path
    # Stub the comparison so no network/browser is needed; we only test that
    # the uploaded page is read and parsed into products.
    seen = {}
    def fake_compare(products, client, **k):
        seen["n"] = len(products)
        return []
    monkeypatch.setattr(dashboard, "compare_products", fake_compare)
    argos = Path("tests/fixtures/argos_flight_search.html").read_bytes()
    out = dashboard._run_scan(
        {"source": "argos", "comparator": "google", "max_products": "5"},
        {"page": _FakeUpload(argos)},
    )
    assert seen["n"] == 3  # 3 products parsed from the fixture and handed to compare
    assert "Parsed 3 products from the uploaded Argos page" in out


def test_dropdowns_render():
    body = dashboard._render()
    assert '<select name="source">' in body
    assert '<select name="comparator">' in body
    assert '<select name="ebay_env">' in body
    assert 'type="file"' in body  # the upload control
    assert "John Lewis (beta)" in body


def test_scan_ebay_requires_credentials():
    out = dashboard._run_scan({"url": "https://www.argos.co.uk/search/x/",
                               "comparator": "ebay", "ebay_id": "", "ebay_secret": ""})
    assert "eBay needs both" in out


def test_empty_results_message():
    assert "No comparisons produced" in dashboard._results_table([], "google", 13.0, 0.0)
