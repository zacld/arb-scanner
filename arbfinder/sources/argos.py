"""Argos (argos.co.uk) search/category page scraper.

Argos search pages are server-rendered with the product data embedded as a
JSON blob (``window.App = {...}`` redux state). Markup and blob shape change
over time, so parsing is layered — each strategy is tried in order until one
yields products:

1. Embedded redux/state JSON (``window.App`` / ``window.__data`` / Next.js
   ``__NEXT_DATA__``), walked generically for product-shaped objects.
2. ``application/ld+json`` blocks (ItemList / Product).
3. Plain HTML product cards via ``data-test`` attributes.

Product detail pages carry JSON-LD with ``gtin13`` (the EAN), which
``fetch_ean`` extracts when deeper matching is wanted.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..http import PoliteSession
from ..models import Product

log = logging.getLogger(__name__)

BASE_URL = "https://www.argos.co.uk"

_STATE_RE = re.compile(
    r"window\.(?:App|__data|__INITIAL_STATE__)\s*=\s*(\{.*?\})\s*(?:;|</script>)",
    re.DOTALL,
)


def _to_price(value) -> float | None:
    """Coerce '£129.99', '129.99', 12999 (pence-free) etc. to a float."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"(\d+(?:\.\d{1,2})?)", str(value).replace(",", ""))
    return float(m.group(1)) if m else None


# --- strategy 1: embedded state JSON ---------------------------------------

def _iter_dicts(node):
    """Yield every dict nested anywhere inside a JSON structure."""
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v)


def _product_from_state_dict(d: dict) -> Product | None:
    """Recognise an Argos redux product object and convert it.

    Historical shape: {"id": "9515520", "attributes": {"name": ..., "brand": ...},
    "prices": {"attributes": [{"amount": "129.99", "type": "now"}, ...]}}
    but we accept looser shapes too (name/price at the top level).
    """
    attrs = d.get("attributes") if isinstance(d.get("attributes"), dict) else {}
    name = attrs.get("name") or d.get("name") or d.get("title")
    if not isinstance(name, str) or not name.strip():
        return None

    price = None
    prices = d.get("prices")
    if isinstance(prices, dict):
        rows = prices.get("attributes")
        if isinstance(rows, list):
            now = [r for r in rows if isinstance(r, dict) and r.get("type") in ("now", "current")]
            row = (now or [r for r in rows if isinstance(r, dict)] or [None])[0]
            if row:
                price = _to_price(row.get("amount") or row.get("value"))
        else:
            price = _to_price(prices.get("now") or prices.get("current"))
    if price is None:
        price = _to_price(d.get("price"))
    if price is None or price <= 0:
        return None

    pid = d.get("id") or d.get("partNumber") or d.get("productId")
    url = d.get("url") or (f"{BASE_URL}/product/{pid}" if pid else "")
    if url and not url.startswith("http"):
        url = urljoin(BASE_URL, url)
    if not url:
        return None

    ean = d.get("ean") or attrs.get("ean") or d.get("gtin13") or d.get("gtin")
    brand = attrs.get("brand") or d.get("brand")
    if isinstance(brand, dict):
        brand = brand.get("name")
    return Product(
        name=name.strip(),
        price=price,
        url=url,
        ean=str(ean) if ean else None,
        brand=brand if isinstance(brand, str) else None,
        model_number=d.get("partNumber") or attrs.get("modelNumber"),
    )


def _parse_state_json(html: str) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()
    blobs = [m.group(1) for m in _STATE_RE.finditer(html)]
    soup = BeautifulSoup(html, "lxml")
    next_data = soup.find("script", id="__NEXT_DATA__")
    if next_data and next_data.string:
        blobs.append(next_data.string)
    for blob in blobs:
        try:
            data = json.loads(blob)
        except json.JSONDecodeError:
            continue
        for d in _iter_dicts(data):
            p = _product_from_state_dict(d)
            if p and p.url not in seen:
                seen.add(p.url)
                products.append(p)
    return products


# --- strategy 2: JSON-LD ----------------------------------------------------

def _products_from_ldjson(data) -> list[Product]:
    out: list[Product] = []
    nodes = data if isinstance(data, list) else [data]
    for node in nodes:
        if not isinstance(node, dict):
            continue
        if node.get("@type") == "ItemList":
            for el in node.get("itemListElement", []):
                item = el.get("item", el) if isinstance(el, dict) else None
                out.extend(_products_from_ldjson(item))
        elif node.get("@type") == "Product":
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = _to_price(offers.get("price") or offers.get("lowPrice"))
            url = node.get("url") or offers.get("url") or ""
            if node.get("name") and price and url:
                brand = node.get("brand")
                if isinstance(brand, dict):
                    brand = brand.get("name")
                out.append(
                    Product(
                        name=node["name"].strip(),
                        price=price,
                        url=urljoin(BASE_URL, url),
                        ean=node.get("gtin13") or node.get("gtin"),
                        model_number=node.get("model") or node.get("mpn"),
                        brand=brand if isinstance(brand, str) else None,
                    )
                )
    return out


def _parse_ldjson(soup: BeautifulSoup) -> list[Product]:
    products: list[Product] = []
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        products.extend(_products_from_ldjson(data))
    return products


# --- strategy 3: HTML cards -------------------------------------------------

_CARD_SELECTORS = [
    '[data-test="component-product-card"]',
    '[data-test="product-card"]',
    "div.ProductCardstyles__Wrapper-sc-1fgptbz-0",
]
_TITLE_SELECTORS = ['[data-test="component-product-card-title"]', '[data-test="product-title"]', "h2", "h3"]
_PRICE_SELECTORS = ['[data-test="component-product-card-price"]', '[data-test="product-price"]', ".prices"]


def _parse_cards(soup: BeautifulSoup) -> list[Product]:
    products: list[Product] = []
    cards = []
    for sel in _CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            break
    for card in cards:
        title_el = next((card.select_one(s) for s in _TITLE_SELECTORS if card.select_one(s)), None)
        price_el = next((card.select_one(s) for s in _PRICE_SELECTORS if card.select_one(s)), None)
        link = card.select_one('a[href*="/product/"]')
        if not (title_el and price_el and link):
            continue
        price = _to_price(price_el.get_text(" ", strip=True))
        if not price:
            continue
        products.append(
            Product(
                name=title_el.get_text(" ", strip=True),
                price=price,
                url=urljoin(BASE_URL, link["href"]),
            )
        )
    return products


# --- public API -------------------------------------------------------------

def parse_search_page(html: str) -> list[Product]:
    """Extract products from an Argos search/category page, trying each strategy."""
    products = _parse_state_json(html)
    if products:
        return products
    soup = BeautifulSoup(html, "lxml")
    products = _parse_ldjson(soup)
    if products:
        return products
    return _parse_cards(soup)


def parse_ean_from_product_page(html: str) -> str | None:
    """Pull gtin13/EAN from a product detail page's JSON-LD (or embedded state)."""
    soup = BeautifulSoup(html, "lxml")
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") == "Product":
                gtin = node.get("gtin13") or node.get("gtin") or node.get("gtin14")
                if gtin:
                    return str(gtin)
    m = re.search(r'"(?:ean|gtin13?)"\s*:\s*"?(\d{8,14})"?', html)
    return m.group(1) if m else None


class ArgosScraper:
    def __init__(self, session: PoliteSession | None = None):
        self.session = session or PoliteSession()

    def scrape(self, url: str, max_products: int | None = None, fetch_ean: bool = False) -> list[Product]:
        """Scrape an Argos search or category URL.

        With ``fetch_ean=True``, also visits each product page (politely, one
        request per product) to pull the EAN for barcode-level matching.
        """
        resp = self.session.get(url)
        resp.raise_for_status()
        products = parse_search_page(resp.text)
        if not products:
            log.warning(
                "No products parsed from %s — page may be JS-rendered or blocked "
                "(status %s, %d bytes). Consider the Playwright fallback.",
                url, resp.status_code, len(resp.text),
            )
        if max_products:
            products = products[:max_products]
        if fetch_ean:
            for p in products:
                if p.ean:
                    continue
                try:
                    detail = self.session.get(p.url)
                    p.ean = parse_ean_from_product_page(detail.text)
                except Exception as exc:  # noqa: BLE001 - one bad page shouldn't kill the run
                    log.warning("Could not fetch EAN for %s: %s", p.url, exc)
        return products
