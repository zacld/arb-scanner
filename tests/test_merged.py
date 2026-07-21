import csv

import pytest

from arbfinder.demo import DemoEbayClient, DemoPriceRunnerClient, demo_products
from arbfinder.models import Comparison, MergedRow, Product
from arbfinder.pipeline import compare_products_multi
from arbfinder.report import format_merged_table, sort_merged, write_merged_csv


def _rows():
    return compare_products_multi(
        demo_products(), [DemoPriceRunnerClient(), DemoEbayClient()]
    )


def test_merge_one_row_per_product_with_both_markets():
    rows = _rows()
    assert len(rows) == 6
    by_name = {r.product.name: r for r in rows}

    lego = by_name["LEGO Technic 42151 Bugatti Bolide Race Car Set"]
    assert lego.ebay is not None and lego.pricerunner is not None
    assert lego.ebay.market_price == pytest.approx(50.48)
    assert lego.pricerunner.market_price == pytest.approx(44.97)

    # Shark: PriceRunner match only (too few credible eBay listings).
    shark = by_name["Shark Anti Hair Wrap Cordless Vacuum Cleaner IZ300UK"]
    assert shark.ebay is None and shark.pricerunner is not None
    assert shark.net_profit(13.0, 0.0) is None
    assert shark.pricerunner_gap == pytest.approx(-20.99)


def test_sort_merged_net_desc_then_na_by_pr_gap():
    rows = sort_merged(_rows())
    names = [r.product.name for r in rows]
    # eBay-matched rows first, best net first; Ninja worst net of those.
    assert "Tommee" in names[0]
    assert "Ninja" in names[4]
    # No-eBay row last regardless of anything else.
    assert "Shark" in names[-1]
    nets = [r.net_profit(13.0, 0.0) for r in rows]
    assert nets[:5] == sorted(nets[:5], reverse=True)
    assert nets[-1] is None


def test_sort_merged_tiebreaks_na_rows_by_pr_gap():
    def na_row(name, argos, pr_price):
        p = Product(name=name, price=argos, url=f"u/{name}")
        pr = Comparison(product=p, market="pricerunner", market_price=pr_price,
                        market_url="m", n_listings=1, matched_by="title",
                        resale_market=False)
        return MergedRow(product=p, pricerunner=pr)

    rows = sort_merged([na_row("small gap", 10.0, 11.0), na_row("big gap", 10.0, 20.0)])
    assert [r.product.name for r in rows] == ["big gap", "small gap"]


def test_merged_table_renders_dashes_for_missing():
    table = format_merged_table(sort_merged(_rows()))
    shark_line = next(l for l in table.splitlines() if "Shark" in l)
    assert shark_line.count("—") == 2  # no eBay price, no net
    assert "PR gap = vs lowest retail ask" in table


def test_merged_csv_round_trip(tmp_path):
    ordered = sort_merged(_rows())
    path = write_merged_csv(ordered, tmp_path / "out.csv")
    with path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 6
    assert rows[0]["product_name"] == ordered[0].product.name
    assert float(rows[0]["net_profit_gbp"]) == pytest.approx(
        ordered[0].net_profit(13.0, 0.0), abs=0.005
    )
    shark = next(r for r in rows if "Shark" in r["product_name"])
    assert shark["ebay_price"] == "" and shark["net_profit_gbp"] == ""
    assert shark["pricerunner_gap_gbp"] == "-20.99"
