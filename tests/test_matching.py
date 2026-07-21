import pytest

from arbfinder.matching import (
    clean_title,
    filter_matches,
    representative_price,
    title_similarity,
)
from arbfinder.models import ComparableListing, Product


def test_clean_title_strips_noise_and_pack_sizes():
    assert clean_title("BRAND NEW! Sony WH-CH520 Headphones (2 Pack) - Free Delivery UK") == (
        "sony wh ch520 headphones"
    )
    assert "pack" not in clean_title("Duracell AA Batteries Pack of 12")
    assert clean_title("Widget x4 bundle") == "widget bundle"


def test_title_similarity_matches_reordered_titles():
    a = "LEGO Technic 42151 Bugatti Bolide Race Car Set"
    b = "LEGO 42151 Technic Bugatti Bolide Racing Car - Brand New Sealed"
    assert title_similarity(a, b) >= 85


def test_title_similarity_rejects_accessories():
    a = "Sony WH-CH520 Wireless On-Ear Headphones - Black"
    b = "Carry Case Cover Pouch for Sony WH-CH520 Headphones"
    assert title_similarity(a, b) < 85


def _listing(title, price, shipping=0.0):
    return ComparableListing(title=title, price=price, shipping=shipping, url="u")


def test_filter_matches_drops_low_score_on_title_search():
    p = Product(name="Sony WH-CH520 Wireless On-Ear Headphones - Black", price=34.99, url="u")
    listings = [
        _listing("Sony WH-CH520 Wireless Headphones Black", 37.99),
        _listing("Carry Case Cover Pouch for Sony WH-CH520 Headphones", 6.99),
    ]
    kept = filter_matches(p, listings, matched_by="title")
    assert len(kept) == 1
    assert kept[0].price == 37.99


def test_filter_matches_lenient_for_ean():
    p = Product(name="Some Product Name", price=10.0, url="u", ean="123")
    kept = filter_matches(p, [_listing("", 12.0)], matched_by="ean")
    assert len(kept) == 1  # untitled listings trusted when barcode-matched


def test_representative_price_is_median_of_delivered_totals():
    listings = [_listing("a", 10.0, 2.0), _listing("b", 11.0), _listing("c", 99.0)]
    price, url = representative_price(listings)
    assert price == 12.0  # totals are 12, 11, 99 -> median 12
    assert url == "u"


def test_representative_price_empty_raises():
    with pytest.raises(ValueError):
        representative_price([])
