from arbfinder.models import ComparableListing, Comparison, Product
from arbfinder.opportunity import (build_opportunity, match_confidence,
                                   opportunity_score, rank_opportunities, roi)


def _comp(name, buy, sell, n=5, matched_by="title", listing_title=None, resale=True):
    listing = ComparableListing(title=listing_title or name, price=sell, shipping=0.0, url="m")
    return Comparison(
        product=Product(name=name, price=buy, url="u"),
        market="ebay" if resale else "google", market_price=sell, market_url="m",
        n_listings=n, matched_by=matched_by, listings=[listing], resale_market=resale,
    )


def test_roi_and_score_weight_profit_and_roi_most():
    # net normalised (£50 cap) + roi (100% cap) dominate at 65%.
    s = opportunity_score(net=50, roi_=1.0, demand=0, match=0, trend=0, seasonal=0)
    assert s == 0.65
    assert roi(10.0, 40.0) == 0.25


def test_build_opportunity_none_for_retail_market():
    assert build_opportunity(_comp("x", 40, 50, resale=False), 13, 0) is None


def test_build_opportunity_populates_financials():
    o = build_opportunity(_comp("Bush Desk Fan", 22.0, 44.99), fees_pct=13, postage=4,
                          trend_strength=0.5, seasonal_strength=0.8)
    # 44.99 - 22 - 13% of 44.99 - 4 = +13.14
    assert o.estimated_net_profit == 13.14
    assert round(o.roi, 3) == round(13.14 / 22.0, 3)
    assert o.match_confidence == 1.0        # listing title == product name
    assert 0 < o.opportunity_score <= 1


def test_thresholds_exclude_before_ranking():
    winner = build_opportunity(_comp("Winner", 40, 60), 13, 0)          # net +12.20
    loser = build_opportunity(_comp("Loser", 100, 101), 13, 0)         # net -12.13
    ranked = rank_opportunities([loser, winner], min_net=0)
    assert [o.retailer_product.name for o in ranked] == ["Winner"]


def test_ranking_is_profit_first_not_trend_first():
    # Big-profit item has zero trend; small-profit item is maximally trendy.
    big = build_opportunity(_comp("Big profit", 40, 80), 13, 0, trend_strength=0.0)
    small = build_opportunity(_comp("Trendy small", 40, 55), 13, 0,
                              trend_strength=1.0, seasonal_strength=1.0)
    ranked = rank_opportunities([small, big])
    assert ranked[0].retailer_product.name == "Big profit"  # profit wins over trend


def test_table_labels_price_as_active_listing_estimate():
    from arbfinder.opportunity import format_opportunities
    o = build_opportunity(_comp("Bush Desk Fan", 22.0, 49.99), 13, 0)
    out = format_opportunities([o])
    assert "ACTIVE listings" in out          # honest label, not "sold"
    assert "verify sold volume" in out.lower()


def test_match_confidence_from_fuzzy_title():
    good = _comp("Sony WH-CH520 Headphones", 30, 40, listing_title="Sony WH-CH520 Wireless Headphones")
    bad = _comp("Sony WH-CH520 Headphones", 30, 40, listing_title="Case for Sony WH-CH520")
    assert match_confidence(good) > match_confidence(bad)
