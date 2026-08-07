"""Mismatch scan buy-side: wide-net Argos category/clearance sweep."""

from arbfinder.mismatch import _page_url, sweep_products
from arbfinder.models import Product


def test_sweep_paginates_and_dedupes():
    pages_served = []

    class _FakeScraper:
        def scrape(self, url):
            pages_served.append(url)
            if "page:3" in url:
                return []  # end of results
            n = url.count("page")  # 0 for p1, 1 for p2
            return [
                Product(name=f"item{n}", price=5.0, url=f"https://argos/item{n}", source="argos"),
                Product(name="shared", price=5.0, url="https://argos/shared", source="argos"),
            ]

    out = sweep_products(_FakeScraper(), "https://www.argos.co.uk/list/sale", pages=3)
    urls = {p.url for p in out}
    assert urls == {"https://argos/item0", "https://argos/item1", "https://argos/shared"}
    assert any("opt/page:2" in u for u in pages_served)  # actually paginated


def test_sweep_respects_max_products():
    class _FakeScraper:
        def scrape(self, url):
            return [Product(name=f"{url}-{i}", price=5.0, url=f"{url}#{i}", source="argos")
                    for i in range(10)]

    out = sweep_products(_FakeScraper(), "https://www.argos.co.uk/list/sale",
                         pages=5, max_products=7)
    assert len(out) == 7


def test_page_url_builder():
    assert _page_url("https://www.argos.co.uk/list/sale", 1) == "https://www.argos.co.uk/list/sale"
    assert _page_url("https://www.argos.co.uk/list/sale", 2) == \
        "https://www.argos.co.uk/list/sale/opt/page:2/"
