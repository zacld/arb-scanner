"""Mismatch scan: wide-net sweep + Amazon-primary / eBay-secondary compare."""

from arbfinder.mismatch import _page_url, compare_mismatch, sweep_products
from arbfinder.models import ComparableListing, Product


class _FakeClient:
    """Duck-types a comparator: returns listings titled exactly like the query."""

    def __init__(self, market, price, n, resale=True, min_listings=1):
        self.market_name = market
        self.resale_market = resale
        self.default_min_listings = min_listings
        self._price = price
        self._n = n

    def search(self, query=None, gtin=None, **_):
        term = query or gtin or ""
        if self._n <= 0:
            return []
        return [
            ComparableListing(title=term, price=self._price, shipping=0.0,
                              url=f"https://{self.market_name}/{i}", condition="",
                              seller=self.market_name)
            for i in range(self._n)
        ]


def _product(name="Widget", price=10.0):
    return Product(name=name, price=price, url="https://argos/widget", source="argos")


def test_prefers_primary_and_attaches_secondary():
    products = [_product()]
    amazon = _FakeClient("amazon", 30.0, 1)          # primary: sells for £30
    ebay = _FakeClient("ebay", 28.0, 3, min_listings=3)  # secondary: £28
    opps = compare_mismatch(products, amazon, ebay, fees=13, postage=0)
    assert len(opps) == 1
    o = opps[0]
    assert o.marketplace_match.market == "amazon"     # primary drove the row
    assert o.expected_sale_price == 30.0
    assert o.secondary_market == "ebay"               # eBay attached for comparison
    assert o.secondary_price == 28.0
    assert o.estimated_net_profit > 0                  # £10 buy → £30 resale


def test_falls_back_to_secondary_when_primary_has_no_match():
    products = [_product()]
    amazon = _FakeClient("amazon", 0.0, 0)             # primary finds nothing
    ebay = _FakeClient("ebay", 25.0, 3, min_listings=3)
    opps = compare_mismatch(products, amazon, ebay, fees=13, postage=0)
    assert len(opps) == 1
    o = opps[0]
    assert o.marketplace_match.market == "ebay"        # fell back to secondary
    assert o.expected_sale_price == 25.0
    assert o.secondary_market is None                  # no phantom primary number


def test_dropped_when_neither_venue_matches():
    products = [_product()]
    amazon = _FakeClient("amazon", 0.0, 0)
    ebay = _FakeClient("ebay", 0.0, 0, min_listings=3)
    assert compare_mismatch(products, amazon, ebay, fees=13, postage=0) == []


def test_sweep_paginates_and_dedupes():
    pages_served = []

    class _FakeScraper:
        def scrape(self, url):
            pages_served.append(url)
            if "page:3" in url:
                return []  # end of results
            # page 1 and 2 each return one unique + one repeat of a shared item
            n = url.count("page")  # 0 for p1, 1 for p2
            return [
                Product(name=f"item{n}", price=5.0, url=f"https://argos/item{n}", source="argos"),
                Product(name="shared", price=5.0, url="https://argos/shared", source="argos"),
            ]

    out = sweep_products(_FakeScraper(), "https://www.argos.co.uk/list/sale", pages=3)
    urls = {p.url for p in out}
    assert urls == {"https://argos/item0", "https://argos/item1", "https://argos/shared"}
    assert any("opt/page:2" in u for u in pages_served)  # actually paginated


def test_page_url_builder():
    assert _page_url("https://www.argos.co.uk/list/sale", 1) == "https://www.argos.co.uk/list/sale"
    assert _page_url("https://www.argos.co.uk/list/sale", 2) == \
        "https://www.argos.co.uk/list/sale/opt/page:2/"
