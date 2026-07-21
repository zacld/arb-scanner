from pathlib import Path

from arbfinder.sources.argos import parse_ean_from_product_page, parse_search_page

DEMO_DATA = Path(__file__).parent.parent / "arbfinder" / "demo_data"
FIXTURES = Path(__file__).parent / "fixtures"


def test_parses_products_from_embedded_state():
    html = (DEMO_DATA / "argos_search.html").read_text()
    products = parse_search_page(html)
    assert len(products) == 6
    lego = products[0]
    assert lego.name == "LEGO Technic 42151 Bugatti Bolide Race Car Set"
    assert lego.price == 39.99  # "now" price, not the "was" price
    assert lego.ean == "5702017424736"
    assert lego.url == "https://www.argos.co.uk/product/9515520"
    assert lego.brand == "LEGO"


def test_products_without_ean_have_none():
    html = (DEMO_DATA / "argos_search.html").read_text()
    products = parse_search_page(html)
    tommee = next(p for p in products if "Tommee" in p.name)
    assert tommee.ean is None


def test_parse_ldjson_fallback():
    html = """
    <html><head><script type="application/ld+json">
    {"@type": "ItemList", "itemListElement": [
      {"@type": "ListItem", "item": {"@type": "Product", "name": "Widget Pro",
       "url": "/product/123", "offers": {"@type": "Offer", "price": "9.99"}}}
    ]}
    </script></head><body></body></html>
    """
    products = parse_search_page(html)
    assert len(products) == 1
    assert products[0].name == "Widget Pro"
    assert products[0].price == 9.99
    assert products[0].url.endswith("/product/123")


def test_parse_html_cards_fallback():
    html = """
    <html><body>
      <div data-test="component-product-card">
        <a href="/product/456"><h2 data-test="component-product-card-title">Gadget X</h2></a>
        <div data-test="component-product-card-price">£24.50</div>
      </div>
    </body></html>
    """
    products = parse_search_page(html)
    assert len(products) == 1
    assert products[0].name == "Gadget X"
    assert products[0].price == 24.50


def test_empty_page_yields_no_products():
    assert parse_search_page("<html><body>Access denied</body></html>") == []


def test_ean_from_product_page_jsonld():
    html = (FIXTURES / "argos_product_page.html").read_text()
    assert parse_ean_from_product_page(html) == "5702017424736"
