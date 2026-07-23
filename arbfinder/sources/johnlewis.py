"""John Lewis (johnlewis.com) search-results parser.

STATUS: best-effort, needs one live verification. John Lewis is a JS-heavy,
bot-protected site (like Argos), so this runs through the same browser
fallback and parses in layered strategies:

1. ``application/ld+json`` Product / ItemList blocks (John Lewis has
   historically emitted these).
2. A generic walk of any embedded JSON state for product-shaped objects
   (name + price + product URL).

If a live scan returns nothing, save the page with ``scripts/probe_page.py``
and inspect it — the parser targets are then a quick adjustment, exactly as
they were for Argos.
"""

from __future__ import annotations

import json
import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Product

log = logging.getLogger(__name__)

BASE_URL = "https://www.johnlewis.com"


def _to_price(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = re.search(r"(\d+(?:\.\d{1,2})?)", str(value).replace(",", ""))
    return float(m.group(1)) if m else None


def _iter_dicts(node):
    if isinstance(node, dict):
        yield node
        for v in node.values():
            yield from _iter_dicts(v)
    elif isinstance(node, list):
        for v in node:
            yield from _iter_dicts(v)


# --- strategy 1: JSON-LD -----------------------------------------------------

def _products_from_ldjson(data) -> list[Product]:
    out: list[Product] = []
    for node in (data if isinstance(data, list) else [data]):
        if not isinstance(node, dict):
            continue
        t = node.get("@type")
        if t == "ItemList":
            for el in node.get("itemListElement", []):
                item = el.get("item", el) if isinstance(el, dict) else None
                out.extend(_products_from_ldjson(item))
        elif t == "Product":
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = _to_price(offers.get("price") or offers.get("lowPrice"))
            url = node.get("url") or (offers.get("url") if isinstance(offers, dict) else "") or ""
            if node.get("name") and price and url:
                brand = node.get("brand")
                if isinstance(brand, dict):
                    brand = brand.get("name")
                out.append(
                    Product(
                        name=node["name"].strip(),
                        price=price,
                        url=urljoin(BASE_URL, url),
                        source="johnlewis",
                        ean=node.get("gtin13") or node.get("gtin"),
                        model_number=node.get("mpn") or node.get("model"),
                        brand=brand if isinstance(brand, str) else None,
                    )
                )
    return out


def _parse_ldjson(soup: BeautifulSoup) -> list[Product]:
    products: list[Product] = []
    seen: set[str] = set()
    for script in soup.find_all("script", type="application/ld+json"):
        if not script.string:
            continue
        try:
            data = json.loads(script.string)
        except json.JSONDecodeError:
            continue
        for p in _products_from_ldjson(data):
            if p.url not in seen:
                seen.add(p.url)
                products.append(p)
    return products


# --- strategy 2: generic embedded state --------------------------------------

def _product_from_state_dict(d: dict) -> Product | None:
    name = d.get("title") or d.get("name") or d.get("productName")
    if not isinstance(name, str) or not name.strip():
        return None
    price = None
    for key in ("price", "nowPrice", "currentPrice", "sellingPrice"):
        node = d.get(key)
        if isinstance(node, dict):
            price = _to_price(node.get("amount") or node.get("value") or node.get("now"))
        else:
            price = _to_price(node)
        if price:
            break
    if not price or price <= 0:
        return None
    pid = d.get("id") or d.get("productId") or d.get("skuId")
    url = d.get("url") or d.get("productUrl") or (f"/p/{pid}" if pid else "")
    if not url:
        return None
    return Product(
        name=name.strip(),
        price=price,
        url=urljoin(BASE_URL, str(url)),
        source="johnlewis",
        ean=d.get("ean") or d.get("gtin13"),
        brand=d.get("brand") if isinstance(d.get("brand"), str) else None,
    )


_STATE_RE = re.compile(
    r"(?:window\.__PRELOADED_STATE__|__NEXT_DATA__|window\.__data)\s*=\s*(\{.*?\})\s*(?:;|</script>)",
    re.DOTALL,
)


def _parse_state_json(html: str, soup: BeautifulSoup) -> list[Product]:
    blobs = [m.group(1) for m in _STATE_RE.finditer(html)]
    nxt = soup.find("script", id="__NEXT_DATA__")
    if nxt and nxt.string:
        blobs.append(nxt.string)
    products: list[Product] = []
    seen: set[str] = set()
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


def parse_search_page(html: str) -> list[Product]:
    """Extract products from a John Lewis search page, trying each strategy."""
    soup = BeautifulSoup(html, "lxml")
    products = _parse_ldjson(soup)
    if products:
        return products
    return _parse_state_json(html, soup)
