import csv

import pytest

from arbfinder.demo import DemoEbayClient, demo_products
from arbfinder.pipeline import compare_products
from arbfinder.report import format_table, sort_comparisons, write_csv


def test_demo_pipeline_end_to_end(tmp_path):
    products = demo_products()
    comparisons = compare_products(products, DemoEbayClient())

    names = [c.product.name for c in comparisons]
    # Shark has only 2 credible listings -> skipped by min_listings.
    assert not any("Shark" in n for n in names)
    assert len(comparisons) == 5

    by_name = {c.product.name: c for c in comparisons}
    lego = by_name["LEGO Technic 42151 Bugatti Bolide Race Car Set"]
    assert lego.matched_by == "ean"
    assert lego.market_price == pytest.approx(50.48)  # median delivered total
    assert lego.diff_abs > 10

    sony = by_name["Sony WH-CH520 Wireless On-Ear Headphones - Black"]
    assert sony.matched_by == "title"
    assert sony.n_listings == 4  # accessory case listing filtered out

    casio = by_name["Casio FX-83GTCW Scientific Calculator - Blue"]
    assert casio.diff_abs < 0  # eBay cheaper -> negative gap, still reported

    # Sorting: biggest £ gap first.
    ordered = sort_comparisons(comparisons)
    assert ordered[0].diff_abs == max(c.diff_abs for c in comparisons)

    # CSV round-trip.
    path = write_csv(ordered, tmp_path / "out.csv")
    with path.open() as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == 5
    assert rows[0]["product_name"] == ordered[0].product.name
    assert float(rows[0]["diff_gbp"]) == round(ordered[0].diff_abs, 2)

    # Console table renders every row.
    table = format_table(ordered)
    assert table.count("\n") == len(comparisons) + 1


def test_min_diff_pct_semantics():
    comparisons = compare_products(demo_products(), DemoEbayClient())
    profitable = [c for c in comparisons if c.diff_pct >= 20]
    assert {c.product.name.split()[0] for c in profitable} == {"Tommee", "LEGO"}
