import json
from unittest.mock import MagicMock

import pytest

from arbfinder.comparators.ebay import EbayAuthError, EbayBrowseClient, parse_item_summaries


def _resp(status=200, payload=None, text=""):
    r = MagicMock()
    r.status_code = status
    r.json.return_value = payload or {}
    r.text = text or json.dumps(payload or {})
    return r


def _client(session):
    return EbayBrowseClient("id", "secret", session=session, min_delay=0)


def test_token_fetched_once_and_cached():
    session = MagicMock()
    session.post.return_value = _resp(payload={"access_token": "tok", "expires_in": 7200})
    session.get.return_value = _resp(payload={"itemSummaries": []})
    client = _client(session)
    client.search(query="widget")
    client.search(query="widget")
    assert session.post.call_count == 1
    auth_header = session.get.call_args.kwargs["headers"]["Authorization"]
    assert auth_header == "Bearer tok"


def test_auth_failure_raises():
    session = MagicMock()
    session.post.return_value = _resp(status=401, text="invalid_client")
    with pytest.raises(EbayAuthError):
        _client(session).search(query="widget")


def test_search_builds_gb_filters_and_gtin_param():
    session = MagicMock()
    session.post.return_value = _resp(payload={"access_token": "tok", "expires_in": 7200})
    session.get.return_value = _resp(payload={"itemSummaries": []})
    client = _client(session)
    client.search(gtin="5702017424736")
    params = session.get.call_args.kwargs["params"]
    assert params["gtin"] == "5702017424736"
    assert "deliveryCountry:GB" in params["filter"]
    assert "priceCurrency:GBP" in params["filter"]
    assert "buyingOptions:{FIXED_PRICE}" in params["filter"]
    headers = session.get.call_args.kwargs["headers"]
    assert headers["X-EBAY-C-MARKETPLACE-ID"] == "EBAY_GB"


def test_search_requires_query_or_gtin():
    with pytest.raises(ValueError):
        _client(MagicMock()).search()


def test_parse_item_summaries_extracts_price_shipping_url():
    payload = {
        "itemSummaries": [
            {
                "title": "Thing",
                "price": {"value": "10.50", "currency": "GBP"},
                "shippingOptions": [{"shippingCost": {"value": "2.99", "currency": "GBP"}}],
                "itemWebUrl": "https://www.ebay.co.uk/itm/1",
                "condition": "New",
            },
            {"title": "No price item"},
        ]
    }
    listings = parse_item_summaries(payload)
    assert len(listings) == 1
    assert listings[0].total == pytest.approx(13.49)


def test_parse_item_summaries_handles_empty_payload():
    assert parse_item_summaries({}) == []
