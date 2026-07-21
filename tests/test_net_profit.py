import csv

import pytest

from arbfinder.demo import DemoEbayClient, DemoPriceRunnerClient, demo_products
from arbfinder.models import Comparison, Product
from arbfinder.pipeline import compare_products
from arbfinder.report import NA_NET, format_table, sort_comparisons, write_csv


def _comparison(price, market_price, resale=True, name="Thing"):
    return Comparison(
        product=Product(name=name, price=price, url="u"),
        market="ebay" if resale else "pricerunner",
        market_price=market_price,
        market_url="m",
        n_listings=3,
        matched_by="title",
        resale_market=resale,
    )


def test_net_profit_formula():
    c = _comparison(price=40.0, market_price=50.0)
    # 50 - 40 - (50 * 15%) - 3.50 = -1.00
    assert c.net_profit(fees_pct=15.0, postage=3.50) == pytest.approx(-1.00)
    assert c.net_profit(fees_pct=0.0, postage=0.0) == pytest.approx(10.00)


def test_net_profit_none_for_retail_comparison():
    assert _comparison(40.0, 50.0, resale=False).net_profit(13.0, 0.0) is None


def test_net_profit_haircut_marks_sale_price_down():
    c = _comparison(price=40.0, market_price=100.0)
    # 5% haircut -> sale 95; fees charged on 95: 95 - 40 - (95*10%) = 45.50
    assert c.net_profit(fees_pct=10.0, postage=0.0, haircut_pct=5.0) == pytest.approx(45.50)
    # 0% haircut is the previous behaviour: 100 - 40 - 10 = 50
    assert c.net_profit(fees_pct=10.0, postage=0.0) == pytest.approx(50.0)


def test_haircut_can_flip_a_thin_margin_negative():
    c = _comparison(price=90.0, market_price=100.0)
    assert c.net_profit(fees_pct=0.0, postage=0.0) == pytest.approx(10.0)
    # a 10% haircut alone wipes the £10 gap
    assert c.net_profit(fees_pct=0.0, postage=0.0, haircut_pct=10.0) == pytest.approx(0.0)


def test_sort_net_puts_na_rows_after_net_rows():
    losing = _comparison(100.0, 105.0, name="losing net")       # net ≈ -8.65
    winning = _comparison(40.0, 60.0, name="winning net")       # net ≈ +12.20
    retail = _comparison(10.0, 90.0, resale=False, name="huge raw gap, N/A")
    ordered = sort_comparisons([retail, losing, winning])
    assert [c.product.name for c in ordered] == [
        "winning net", "losing net", "huge raw gap, N/A"
    ]


def test_demo_ebay_net_sorting_demotes_fee_eaten_gap():
    comparisons = compare_products(demo_products(), DemoEbayClient())
    ordered = sort_comparisons(comparisons)  # defaults: net, 13%, £0
    names = [c.product.name for c in ordered]
    # Ninja has the 2nd-biggest raw £ gap (+12.50) but 13% fees on a £212
    # sale price make it the worst net (-15.12) -> sorts last.
    assert "Ninja" in names[-1]
    assert "Tommee" in names[0]


def test_table_shows_net_and_footnotes():
    ebay_table = format_table([_comparison(40.0, 50.0)], fees_pct=13.0, postage=0.0)
    assert "+3.50" in ebay_table  # 50 - 40 - 6.50
    assert "13% selling fees" in ebay_table
    pr_table = format_table([_comparison(40.0, 50.0, resale=False)])
    assert "N/A" in pr_table
    assert "retail comparison only" in pr_table


def test_csv_net_column(tmp_path):
    rows_in = [_comparison(40.0, 50.0), _comparison(40.0, 50.0, resale=False)]
    path = write_csv(rows_in, tmp_path / "out.csv", fees_pct=13.0, postage=1.0)
    with path.open() as f:
        rows = list(csv.DictReader(f))
    assert rows[0]["net_profit_gbp"] == "2.50"  # 50 - 40 - 6.50 - 1.00
    assert rows[1]["net_profit_gbp"] == NA_NET


def test_pricerunner_rows_never_get_net():
    comparisons = compare_products(demo_products(), DemoPriceRunnerClient())
    assert all(c.net_profit(13.0, 0.0) is None for c in comparisons)
