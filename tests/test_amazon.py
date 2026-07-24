from pathlib import Path

from arbfinder.comparators.amazon import AmazonClient, parse_amazon_results
from arbfinder.models import Product
from arbfinder.pipeline import compare_products


def _fixture() -> str:
    return Path("tests/fixtures/amazon_search.html").read_text(encoding="utf-8")


def test_parses_priced_results():
    listings = parse_amazon_results(_fixture())
    # Two priced, distinct ASINs; the no-price and duplicate rows are dropped.
    assert len(listings) == 2
    black = next(l for l in listings if "Black Desk Fan" in l.title)
    assert black.price == 48.99
    assert black.url == "https://www.amazon.co.uk/dp/B08DESKFAN1"
    assert black.seller == "Amazon"
    assert black.shipping == 0.0


def test_skips_unavailable_and_dedupes():
    listings = parse_amazon_results(_fixture())
    asins = [l.url.rsplit("/", 1)[-1] for l in listings]
    assert "B08NOPRICE3" not in asins            # no price -> skipped
    assert asins.count("B08DESKFAN1") == 1        # duplicate collapsed


def test_client_attributes_mark_resale_market():
    # Amazon price is a sell price, so net profit must be computed (unlike Google).
    assert AmazonClient.market_name == "amazon"
    assert AmazonClient.resale_market is True
    assert AmazonClient.default_min_listings == 1


def test_pipeline_computes_net_off_amazon(monkeypatch):
    # A stub client returning the fixture listings flows through compare_products
    # and yields a resale Comparison with a real net figure.
    class _Stub:
        market_name = "amazon"
        resale_market = True
        default_min_listings = 1

        def search(self, query=None, gtin=None, **k):
            return parse_amazon_results(_fixture())

    product = Product(name="Bush Black Desk Fan - 12 Inch", price=22.00, url="argos://x")
    comps = compare_products([product], _Stub())  # default fuzzy threshold (85)
    assert len(comps) == 1
    c = comps[0]
    assert c.market == "amazon"
    assert c.resale_market is True
    # 48.99 - 22 - 13% of 48.99 - 0 = +£20.62
    assert round(c.net_profit(13.0, 0.0), 2) == 20.62
