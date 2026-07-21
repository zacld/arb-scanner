from pathlib import Path

import pytest

from arbfinder.comparators.google_shopping import (
    _looks_blocked,
    parse_shopping_results,
)
from arbfinder.matching import filter_matches
from arbfinder.models import Product

HTML = (Path(__file__).parent / "fixtures" / "google_shopping.html").read_text()


def test_parses_one_listing_per_retailer():
    listings = parse_shopping_results(HTML)
    sellers = {l.seller for l in listings}
    # SharkNinja rendered twice (grid+list) -> de-duplicated to one.
    assert sellers == {"SharkNinja UK", "Currys", "Argos", "eBay", "QVC UK"}
    assert len(listings) == 5


def test_extracts_price_title_retailer():
    by_seller = {l.seller: l for l in parse_shopping_results(HTML)}
    currys = by_seller["Currys"]
    assert currys.price == 159.99
    assert "AF400UKWH" in currys.title
    assert currys.url.startswith("https://www.google.com/aclk")

    ebay = by_seller["eBay"]
    assert ebay.price == 151.50  # delivery shown separately; price is the item ask


def test_filter_chrome_and_was_price_ignored():
    by_seller = {l.seller: l for l in parse_shopping_results(HTML)}
    shark = by_seller["SharkNinja UK"]
    assert shark.price == 179.99  # not £230 (was) and not a filter value
    assert "by SharkNinja" not in shark.title  # trailing "by <brand>" stripped


def test_coupon_overlay_tile_still_parses_real_price():
    by_seller = {l.seller: l for l in parse_shopping_results(HTML)}
    qvc = by_seller["QVC UK"]
    assert qvc.price == 169.96  # not the "£18 off" promo, not £229 was-price
    assert "off" not in qvc.title.lower()


def test_results_fuzzy_match_the_source_product():
    # The whole point: these Google offers match an Argos AF400UK product.
    product = Product(name="Ninja Foodi MAX Dual Zone AF400UK 9.5L Air Fryer - Black",
                      price=179.99, url="u")
    kept = filter_matches(product, parse_shopping_results(HTML), matched_by="title")
    # SharkNinja/Currys/Argos/QVC AF400UK variants match; unrelated ones wouldn't.
    assert len(kept) >= 4


def test_blocked_detection():
    assert _looks_blocked("<html><body>Our systems have detected unusual traffic</body></html>")
    assert not _looks_blocked("<html><body>Ninja Air Fryer £179.99</body></html>")
