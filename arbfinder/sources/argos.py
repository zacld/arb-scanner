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

from ..browser import BrowserFetcher
from ..http import PoliteSession, RobotsDisallowed
from ..models import Product

log = logging.getLogger(__name__)


class ScrapeBlocked(Exception):
    """The site refused both the plain-HTTP fetch and the browser fallback."""


_DENIAL_MARKERS = ("access denied", "reference #", "request unsuccessful", "captcha")


def looks_like_denial_page(html: str) -> bool:
    """Heuristic for bot-protection denial/challenge pages (Akamai et al.)."""
    lowered = html[:5000].lower()
    return any(marker in lowered for marker in _DENIAL_MARKERS)

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


# --- strategy 1b: Next.js flight data (current Argos frontend, 2026) --------
#
# Argos now server-renders through Next.js App Router: product data arrives in
# React "flight" chunks pushed via self.__next_f.push([1,"..."]) — JS string
# literals that concatenate into a stream containing rows like
#   20:[["$","$L21",null,{"pageProps":{"productData":[{...}, ...]}}]]
# Only a handful of product cards exist in the DOM (the rest hydrate on
# scroll), so this stream is the authoritative source.

_NEXT_F_RE = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', re.DOTALL)

_PRICE_KEY_BLOCKLIST = ("monthly", "credit", "instal", "finance", "was", "rrp", "saving", "deposit")


def decode_flight_text(html: str) -> str:
    """Concatenate and unescape every __next_f string chunk in the page."""
    parts = []
    for m in _NEXT_F_RE.finditer(html):
        try:
            parts.append(json.loads('"' + m.group(1) + '"'))
        except json.JSONDecodeError:
            continue
    return "".join(parts)


def extract_flight_product_dicts(html: str) -> list[dict]:
    """Pull every raw item out of "productData":[...] arrays in the flight stream."""
    text = decode_flight_text(html)
    if not text:
        return []
    decoder = json.JSONDecoder()
    out: list[dict] = []
    for m in re.finditer(r'"productData"\s*:\s*', text):
        try:
            arr, _ = decoder.raw_decode(text, m.end())
        except json.JSONDecodeError:
            continue
        if isinstance(arr, list):
            out.extend(d for d in arr if isinstance(d, dict))
    return out


def _hunt_price(node, max_depth: int = 6) -> float | None:
    """Find the most plausible current-price value anywhere in a product dict.

    The exact field name varies between Argos frontend releases, so instead of
    hardcoding one path we walk the structure: keys containing 'price' (or
    now/current/amount/value beneath a price-ish parent) are candidates; paths
    mentioning credit/monthly/was/RRP etc. are skipped so we never mistake a
    finance instalment or crossed-out price for the selling price.
    """
    candidates: list[tuple[list[str], float]] = []

    def walk(n, path):
        if len(path) > max_depth:
            return
        if isinstance(n, dict):
            # Price rows often carry their kind as a value, not a key:
            # {"amount": "249.99", "type": "was"} must not win over "now".
            kind = str(n.get("type") or n.get("priceType") or "").lower()
            if any(bad in kind for bad in _PRICE_KEY_BLOCKLIST):
                return
            if kind in ("now", "current"):
                path = path + ["now"]
            for k, v in n.items():
                kl = str(k).lower()
                if any(bad in kl for bad in _PRICE_KEY_BLOCKLIST):
                    continue
                if isinstance(v, (dict, list)):
                    walk(v, path + [kl])
                else:
                    priceish = "price" in kl or (
                        kl in ("now", "current", "amount", "value")
                        and any("price" in seg for seg in path)
                    )
                    if priceish:
                        p = _to_price(v)
                        if p is not None and 0.5 <= p <= 100000:
                            candidates.append((path + [kl], p))
        elif isinstance(n, list):
            for v in n:
                walk(v, path)

    walk(node, [])
    if not candidates:
        return None

    def score(c):
        joined = " ".join(c[0])
        return ("now" in joined or "current" in joined) * 2 + ("price" in joined)

    return max(candidates, key=score)[1]


def _flight_price(attrs: dict) -> float | None:
    """Current selling price from a flight product's attributes.

    Confirmed shape of the 2026 Argos search payload: ``attributes.price`` is
    the current price as a number and ``attributes.wasPrice`` the pre-discount
    price (0 when not on offer). We read ``price`` directly and fall back to
    the generic hunter only if that field is ever renamed or missing.
    """
    p = _to_price(attrs.get("price"))
    if p is not None and p > 0:
        return p
    return _hunt_price(attrs)


def _parse_flight_products(html: str, soup: BeautifulSoup) -> list[Product]:
    raw = extract_flight_product_dicts(html)
    if not raw:
        return []
    card_prices = _card_price_map(soup)
    products: list[Product] = []
    seen: set[str] = set()
    for d in raw:
        pid = str(d.get("id") or d.get("productId") or "")
        attrs = d.get("attributes") if isinstance(d.get("attributes"), dict) else {}
        name = attrs.get("name") or attrs.get("title") or d.get("title") or d.get("name")
        if not pid or pid in seen or not isinstance(name, str) or not name.strip():
            continue
        price = _flight_price(attrs) or _hunt_price(d) or card_prices.get(pid)
        if price is None:
            log.debug("Flight product %s (%s) has no findable price; skipping", pid, name)
            continue
        seen.add(pid)
        ean = attrs.get("ean") or attrs.get("gtin13") or attrs.get("gtin") or d.get("ean")
        brand = attrs.get("brand") or d.get("brand")
        products.append(
            Product(
                name=name.strip(),
                price=price,
                url=f"{BASE_URL}/product/{pid}",
                ean=str(ean) if ean else None,
                brand=brand if isinstance(brand, str) else None,
                model_number=attrs.get("modelNumber") or attrs.get("partNumber"),
            )
        )
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
_PRICE_SELECTORS = [
    '[data-test="component-product-card-price"]',
    '[data-test="product-price"]',
    ".ds-c-price",
    '[data-test*="price"]',
    ".prices",
]
# Subtrees that contain £ figures which are NOT the selling price.
_PRICE_NOISE_SELECTORS = ['[data-test*="monthly"]', '[data-test*="credit"]', "del", "s"]
_POUND_RE = re.compile(r"£\s*([\d,]+(?:\.\d{1,2})?)")


def _card_price(card) -> float | None:
    """Selling price from a card: known selectors first, then the first £
    figure in its text after stripping finance/was-price subtrees."""
    for sel in _PRICE_NOISE_SELECTORS:
        for el in card.select(sel):
            el.decompose()
    for sel in _PRICE_SELECTORS:
        el = card.select_one(sel)
        if el:
            price = _to_price(el.get_text(" ", strip=True))
            if price:
                return price
    m = _POUND_RE.search(card.get_text(" ", strip=True))
    return float(m.group(1).replace(",", "")) if m else None


def _find_cards(soup: BeautifulSoup) -> list:
    for sel in _CARD_SELECTORS:
        cards = soup.select(sel)
        if cards:
            return cards
    return []


def _card_price_map(soup: BeautifulSoup) -> dict[str, float]:
    """Map data-product-id -> price for every rendered card that shows one."""
    out: dict[str, float] = {}
    for card in _find_cards(soup):
        pid = card.get("data-product-id")
        if not pid or pid in out:
            continue
        price = _card_price(card)
        if price:
            out[str(pid)] = price
    return out


def _parse_cards(soup: BeautifulSoup) -> list[Product]:
    products: list[Product] = []
    for card in _find_cards(soup):
        title_el = next((card.select_one(s) for s in _TITLE_SELECTORS if card.select_one(s)), None)
        link = card.select_one('a[href*="/product/"]')
        if not (title_el and link):
            continue
        price = _card_price(card)
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
    products = _parse_flight_products(html, soup)
    if products:
        return products
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
    # Where the last unparseable page gets saved for diagnosis.
    debug_dump_path = "debug_argos_page.html"

    def __init__(self, session: PoliteSession | None = None, browser: BrowserFetcher | None = None):
        self.session = session or PoliteSession()
        # Created lazily on first 403/empty page unless one was injected.
        self.browser = browser
        self._used_browser = False

    def _browser_html(self, url: str) -> str | None:
        """Fetch a rendered page via Playwright, still honouring robots.txt."""
        if not self.session.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")
        if self.browser is None:
            self.browser = BrowserFetcher(
                min_delay=self.session.min_delay, jitter=self.session.jitter
            )
        return self.browser.fetch(url)

    def _fetch_html(self, url: str) -> str | None:
        """Plain HTTP first; on a 4xx/5xx or an empty-looking page, fall back
        to the rendered browser. Returns None only if both routes fail."""
        if self._used_browser:
            # The site already refused plain HTTP once — don't keep poking it.
            return self._browser_html(url)
        resp = self.session.get(url)
        if resp.status_code == 200:
            return resp.text
        log.warning(
            "HTTP %s from %s — falling back to Playwright browser rendering "
            "(Argos blocks plain HTTP clients)", resp.status_code, url,
        )
        html = self._browser_html(url)
        if html is not None:
            self._used_browser = True
        return html

    def scrape(self, url: str, max_products: int | None = None, fetch_ean: bool = False) -> list[Product]:
        """Scrape an Argos search or category URL.

        With ``fetch_ean=True``, also visits each product page (politely, one
        request per product) to pull the EAN for barcode-level matching.
        """
        try:
            products: list[Product] = []
            html = self._fetch_html(url)
            if html is not None:
                products = parse_search_page(html)
                if not products and not self._used_browser:
                    log.warning(
                        "Page fetched but no product data found in %d bytes — "
                        "retrying with browser rendering", len(html),
                    )
                    html = self._browser_html(url)
                    if html:
                        self._used_browser = True
                        products = parse_search_page(html)
            if not products:
                if self.browser is not None and self.browser.unavailable_reason:
                    reason = self.browser.unavailable_reason
                elif html and looks_like_denial_page(html):
                    reason = (
                        "bot protection served an Access Denied page to the "
                        "headless browser"
                    )
                else:
                    reason = "the rendered page contained no recognisable product data"
                dump_note = ""
                if html:
                    try:
                        from pathlib import Path
                        Path(self.debug_dump_path).write_text(html, encoding="utf-8")
                        dump_note = (
                            f"\nThe fetched page was saved to {self.debug_dump_path} "
                            "— share that file to diagnose."
                        )
                    except OSError as exc:
                        log.warning("Could not write debug dump: %s", exc)
                raise ScrapeBlocked(
                    f"Could not scrape {url}.\n"
                    f"Plain HTTP was refused and the browser fallback failed: {reason}\n"
                    "If Playwright is installed and this persists, try --show-browser "
                    "(a visible browser window passes bot checks more reliably than "
                    f"headless).{dump_note}"
                )
            if max_products:
                products = products[:max_products]
            if fetch_ean:
                for p in products:
                    if p.ean:
                        continue
                    try:
                        detail_html = self._fetch_html(p.url)
                        if detail_html:
                            p.ean = parse_ean_from_product_page(detail_html)
                    except Exception as exc:  # noqa: BLE001 - one bad page shouldn't kill the run
                        log.warning("Could not fetch EAN for %s: %s", p.url, exc)
            return products
        finally:
            if self.browser is not None:
                self.browser.close()
