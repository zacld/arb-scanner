from pathlib import Path

from bs4 import BeautifulSoup

from arbfinder.sources.argos import (
    _card_price_map,
    _hunt_price,
    decode_flight_text,
    extract_flight_product_dicts,
    parse_search_page,
)

FLIGHT_HTML = (Path(__file__).parent / "fixtures" / "argos_flight_search.html").read_text()


def test_decode_flight_text_concatenates_chunks():
    text = decode_flight_text(FLIGHT_HTML)
    # The productData array is split across two push() chunks; after decoding
    # the concatenated stream must contain the joined array.
    assert '"productData"' in text
    assert '"3117488"' in text  # from the second chunk


def test_extract_flight_product_dicts_spanning_chunks():
    dicts = extract_flight_product_dicts(FLIGHT_HTML)
    ids = {d["id"] for d in dicts}
    assert ids == {"9523997", "3117488", "3632626"}


def test_hunt_price_prefers_now_over_was_and_monthly():
    dicts = {d["id"]: d for d in extract_flight_product_dicts(FLIGHT_HTML)}
    ninja = dicts["9523997"]["attributes"]
    # now=199.99, was=249.99, monthlyPayment=9.51 -> must pick 199.99
    assert _hunt_price(ninja) == 199.99
    tefal = dicts["3632626"]["attributes"]
    # now=89.99, creditAttributes.monthlyPrice=4.28 -> must pick 89.99
    assert _hunt_price(tefal) == 89.99


def test_parse_search_page_uses_flight_data():
    products = parse_search_page(FLIGHT_HTML)
    by_id = {p.url.rsplit("/", 1)[-1]: p for p in products}
    assert set(by_id) == {"9523997", "3117488", "3632626"}

    ninja = by_id["9523997"]
    assert ninja.name == "Ninja Foodi MAX Dual Zone AF400UK 9.5L Air Fryer - Black"
    assert ninja.price == 199.99  # not 249.99 (was) or 9.51 (monthly)
    assert ninja.ean == "0622356254529"
    assert ninja.brand == "Ninja"
    assert ninja.url == "https://www.argos.co.uk/product/9523997"

    # Product only in flight data (not DOM-rendered) still parsed.
    assert by_id["3632626"].price == 89.99


def test_card_price_map_ignores_monthly_payment():
    soup = BeautifulSoup(FLIGHT_HTML, "lxml")
    prices = _card_price_map(soup)
    assert prices["9523997"] == 199.99  # the £9.51/mo must not win
    assert prices["3117488"] == 45.00
