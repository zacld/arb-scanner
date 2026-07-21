from unittest.mock import MagicMock

import pytest

from arbfinder.comparators.pricerunner import (
    SEARCH_URL,
    PriceRunnerClient,
    parse_search_response,
)
from arbfinder.demo import DemoPriceRunnerClient, demo_products
from arbfinder.http import RobotsDisallowed
from arbfinder.pipeline import compare_products


def test_parse_search_response_extracts_fields():
    payload = {
        "products": [
            {"name": "Widget Pro", "lowestPrice": {"amount": "19.99", "currency": "GBP"},
             "url": "/pl/1-2/Widget-Pro-Compare-Prices"},
            {"name": "No price product"},
            {"lowestPrice": {"amount": "5.00"}},
        ]
    }
    listings = parse_search_response(payload)
    assert len(listings) == 1
    l = listings[0]
    assert l.title == "Widget Pro"
    assert l.price == 19.99
    assert l.shipping == 0.0  # delivery cost not in search response
    assert l.url == "https://www.pricerunner.com/pl/1-2/Widget-Pro-Compare-Prices"


def test_parse_search_response_flat_price_shape():
    payload = {"products": [{"name": "X", "price": "7.50", "url": "https://example/x"}]}
    assert parse_search_response(payload)[0].price == 7.50


def test_parse_search_response_empty_payload():
    assert parse_search_response({}) == []


def _resp(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload if payload is not None else {}
    r.text = text
    return r


def test_client_sends_ean_as_query():
    session = MagicMock()
    session.get.return_value = _resp(payload={"products": []})
    PriceRunnerClient(session).search(gtin="5702017424736")
    args, kwargs = session.get.call_args
    assert args[0] == SEARCH_URL
    assert kwargs["params"]["q"] == "5702017424736"


def test_client_requires_query_or_gtin():
    with pytest.raises(ValueError):
        PriceRunnerClient(MagicMock()).search()


def test_client_returns_empty_on_http_error():
    session = MagicMock()
    session.get.return_value = _resp(status=403, text="blocked")
    assert PriceRunnerClient(session).search(query="widget") == []


def test_client_respects_robots_disallow():
    session = MagicMock()
    session.get.side_effect = RobotsDisallowed("nope")
    client = PriceRunnerClient(session)
    assert client.search(query="widget") == []
    assert client.search(query="other") == []  # doesn't blow up on repeat calls


def test_demo_pipeline_with_pricerunner():
    comparisons = compare_products(demo_products(), DemoPriceRunnerClient())
    assert len(comparisons) == 6  # aggregated rows: min_listings defaults to 1
    by_name = {c.product.name: c for c in comparisons}

    lego = by_name["LEGO Technic 42151 Bugatti Bolide Race Car Set"]
    assert lego.market == "pricerunner"
    assert lego.matched_by == "ean"
    assert lego.market_price == pytest.approx(44.97)

    sony = by_name["Sony WH-CH520 Wireless On-Ear Headphones - Black"]
    assert sony.n_listings == 1  # ear-pads accessory fuzzy-filtered out
    assert sony.market_price == pytest.approx(32.95)

    shark = by_name["Shark Anti Hair Wrap Cordless Vacuum Cleaner IZ300UK"]
    assert shark.diff_abs < 0  # cheaper elsewhere -> negative gap reported


def test_explicit_min_listings_still_overrides_default():
    comparisons = compare_products(demo_products(), DemoPriceRunnerClient(), min_listings=2)
    assert comparisons == []  # every fixture query yields a single credible product
