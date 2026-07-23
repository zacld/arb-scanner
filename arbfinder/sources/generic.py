"""Reusable parser for retail sites that emit standard structured data.

Most UK retailers embed products either as schema.org ``application/ld+json``
(Product / ItemList) or in a JS state blob (``__NEXT_DATA__`` /
``__PRELOADED_STATE__`` / ``window.__data``). ``make_parser`` returns a
``parse(html) -> list[Product]`` for a given site, so adding a source is just a
name, a search-URL builder, and (usually) this parser — no new parsing code.
"""

from __future__ import annotations

import json
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import Product

_STATE_RE = re.compile(
    r"(?:window\.__PRELOADED_STATE__|window\.__data|__NEXT_DATA__|window\.__INITIAL_STATE__)"
    r"\s*=\s*(\{.*?\})\s*(?:;|</script>)",
    re.DOTALL,
)
_PRICE_KEYS = ("price", "nowPrice", "currentPrice", "sellingPrice", "amount", "value")


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


def _brand(node) -> str | None:
    b = node.get("brand")
    if isinstance(b, dict):
        b = b.get("name")
    return b if isinstance(b, str) else None


def _products_from_ldjson(data, source: str, base_url: str) -> list[Product]:
    out: list[Product] = []
    for node in (data if isinstance(data, list) else [data]):
        if not isinstance(node, dict):
            continue
        t = node.get("@type")
        types = t if isinstance(t, list) else [t]
        if "ItemList" in types:
            for el in node.get("itemListElement", []):
                item = el.get("item", el) if isinstance(el, dict) else None
                out.extend(_products_from_ldjson(item, source, base_url))
        elif "Product" in types:
            offers = node.get("offers") or {}
            if isinstance(offers, list):
                offers = offers[0] if offers else {}
            price = _to_price((offers or {}).get("price") or (offers or {}).get("lowPrice"))
            url = node.get("url") or (offers.get("url") if isinstance(offers, dict) else "") or ""
            if node.get("name") and price and url:
                out.append(Product(
                    name=str(node["name"]).strip(),
                    price=price,
                    url=urljoin(base_url, url),
                    source=source,
                    ean=node.get("gtin13") or node.get("gtin"),
                    model_number=node.get("mpn") or node.get("model"),
                    brand=_brand(node),
                ))
    return out


def _product_from_state_dict(d: dict, source: str, base_url: str) -> Product | None:
    name = d.get("title") or d.get("name") or d.get("productName") or d.get("displayName")
    if not isinstance(name, str) or not name.strip():
        return None
    price = None
    for key in _PRICE_KEYS:
        if key not in d:
            continue
        node = d[key]
        price = _to_price(node.get("amount") or node.get("value") or node.get("now")
                          if isinstance(node, dict) else node)
        if price:
            break
    if not price or price <= 0:
        return None
    pid = d.get("id") or d.get("productId") or d.get("skuId") or d.get("sku")
    url = d.get("url") or d.get("productUrl") or d.get("link") or (f"/p/{pid}" if pid else "")
    if not url:
        return None
    return Product(
        name=name.strip(), price=price, url=urljoin(base_url, str(url)), source=source,
        ean=d.get("ean") or d.get("gtin13"),
        brand=d["brand"] if isinstance(d.get("brand"), str) else None,
    )


def make_parser(source: str, base_url: str):
    """Return a ``parse(html) -> list[Product]`` for one retail source."""

    def parse(html: str) -> list[Product]:
        soup = BeautifulSoup(html, "lxml")
        products: list[Product] = []
        seen: set[str] = set()

        # 1. JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            if not script.string:
                continue
            try:
                data = json.loads(script.string)
            except json.JSONDecodeError:
                continue
            for p in _products_from_ldjson(data, source, base_url):
                if p.url not in seen:
                    seen.add(p.url)
                    products.append(p)
        if products:
            return products

        # 2. Embedded JS state
        blobs = [m.group(1) for m in _STATE_RE.finditer(html)]
        nxt = soup.find("script", id="__NEXT_DATA__")
        if nxt and nxt.string:
            blobs.append(nxt.string)
        for blob in blobs:
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue
            for node in _iter_dicts(data):
                p = _product_from_state_dict(node, source, base_url)
                if p and p.url not in seen:
                    seen.add(p.url)
                    products.append(p)
        return products

    return parse
