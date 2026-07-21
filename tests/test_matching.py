import pytest

from arbfinder.matching import (
    clean_title,
    filter_matches,
    is_multipack,
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


# --- multipack rejection ---------------------------------------------------

@pytest.mark.parametrize("title", [
    "Ninja AF100UK Air Fryer 2 Pack",
    "Duracell AA Batteries Pack of 12",
    "Sony Headphones Twin Pack",
    "Widget x4 Bundle",
    "Widget Set of 3",
    "Gadget Multipack",
])
def test_is_multipack_detects_quantities(title):
    assert is_multipack(title)


@pytest.mark.parametrize("title", [
    "Ninja AF100UK Air Fryer",
    "AA Battery Pack",            # "pack" with no quantity is not a multipack
    "Sony WH-CH520 Headphones",
    "1 Pack Widget",
])
def test_is_multipack_ignores_singles(title):
    assert not is_multipack(title)


def test_filter_drops_multipack_inflating_listings():
    p = Product(name="Ninja AF100UK Air Fryer", price=79.99, url="u")
    listings = [
        _listing("Ninja AF100UK Air Fryer", 105.0),
        _listing("Ninja AF100UK Air Fryer 2 Pack Bundle", 199.0),
    ]
    kept = filter_matches(p, listings, matched_by="title")
    assert [l.price for l in kept] == [105.0]


def test_filter_keeps_multipack_when_product_is_a_pack():
    p = Product(name="Duracell AA Batteries Pack of 12", price=8.0, url="u")
    kept = filter_matches(
        p, [_listing("Duracell AA Batteries Pack of 12", 11.0)], matched_by="title"
    )
    assert len(kept) == 1


# --- model-number-aware matching -------------------------------------------

def test_exact_model_number_is_trusted_over_weak_title():
    # Title fuzz alone would fail (very different wording), but the exact model
    # number pins it as the same product.
    p = Product(name="Ninja Air Fryer 3.8L", price=79.99, url="u",
                model_number="AF100UK")
    kept = filter_matches(
        p, [_listing("Genuine AF100UK cooker unit boxed", 99.0)], matched_by="title"
    )
    assert len(kept) == 1


def test_different_variant_model_is_rejected():
    p = Product(name="Ninja AF100UK Air Fryer", price=79.99, url="u",
                model_number="AF100UK")
    listings = [
        _listing("Ninja AF100UK Air Fryer", 105.0),
        _listing("Ninja AF300UK Air Fryer Dual Zone", 149.0),  # same family, wrong model
    ]
    kept = filter_matches(p, listings, matched_by="title")
    assert [l.title for l in kept] == ["Ninja AF100UK Air Fryer"]


def test_model_number_tolerates_punctuation_differences():
    p = Product(name="Sony Wireless Headphones", price=34.99, url="u",
                model_number="WH-CH520")
    kept = filter_matches(
        p, [_listing("Sony WHCH520 On-Ear Headphones Black", 39.0)], matched_by="title"
    )
    assert len(kept) == 1


def test_no_model_in_title_falls_back_to_fuzzy():
    # Listing has no model token at all -> neither trusted nor variant-rejected;
    # the fuzzy title gate still admits a clearly-matching title.
    p = Product(name="Sony WH-CH520 Wireless On-Ear Headphones - Black", price=34.99,
                url="u", model_number="WH-CH520")
    kept = filter_matches(
        p, [_listing("Sony Wireless On-Ear Headphones Black", 39.0)], matched_by="title"
    )
    assert len(kept) == 1
