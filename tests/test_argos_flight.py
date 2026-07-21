from pathlib import Path

from bs4 import BeautifulSoup

from arbfinder.sources.argos import (
    _card_price_map,
    _flight_price,
    _hunt_price,
    decode_flight_text,
    extract_flight_product_dicts,
    parse_search_page,
)

FLIGHT_HTML = (Path(__file__).parent / "fixtures" / "argos_flight_search.html").read_text()


def _attrs_by_id():
    return {d["id"]: d["attributes"] for d in extract_flight_product_dicts(FLIGHT_HTML)}


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


def test_flight_price_reads_price_field_not_was_or_credit():
    attrs = _attrs_by_id()
    # 9523997: price 179.99, wasPrice 0, creditAttributes says £7.23/mo
    assert _flight_price(attrs["9523997"]) == 179.99
    # 3117488: price 45.00 while wasPrice 60.00 (on offer) -> take current price
    assert _flight_price(attrs["3117488"]) == 45.00


def test_hunt_price_fallback_still_avoids_traps():
    # Even the generic fallback must dodge wasPrice / monthly credit figures.
    attrs = _attrs_by_id()["3117488"]
    assert _hunt_price(attrs) == 45.00  # not 60.00 (was) or 2.15 (monthly)


def test_parse_search_page_uses_flight_data():
    products = parse_search_page(FLIGHT_HTML)
    by_id = {p.url.rsplit("/", 1)[-1]: p for p in products}
    assert set(by_id) == {"9523997", "3117488", "3632626"}

    ninja = by_id["9523997"]
    assert ninja.name == "Ninja Foodi MAX Dual Zone AF400UK 9.5L Air Fryer - Black"
    assert ninja.price == 179.99  # not wasPrice, not the £7.23/mo credit figure
    assert ninja.brand == "Ninja"
    assert ninja.url == "https://www.argos.co.uk/product/9523997"
    # Argos search flight data carries no EAN (title matching handles it).
    assert ninja.ean is None

    # Discounted product takes current price, not the struck-through was price.
    assert by_id["3117488"].price == 45.00
    # Product only in flight data (not DOM-rendered) still parsed.
    assert by_id["3632626"].price == 89.99


def test_card_price_map_ignores_monthly_payment():
    soup = BeautifulSoup(FLIGHT_HTML, "lxml")
    prices = _card_price_map(soup)
    assert prices["9523997"] == 179.99  # the £7.23/mo must not win
    assert prices["3117488"] == 45.00
