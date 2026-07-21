"""PriceRunner UK comparator — no API key or auth required.

Chosen over Google Shopping: PriceRunner's own frontend is fed by a public
JSON search endpoint, so we get clean structured data (product name, lowest
tracked retailer price, product URL) with a plain GET — no JS rendering and
none of Google Shopping's aggressive bot defences / obfuscated markup.

Caveats vs the eBay comparator:
- Results are *aggregated catalog products* (one row per product, priced at
  the lowest current retailer offer), not individual listings — so a single
  match is already meaningful, hence ``default_min_listings = 1``.
- The search response carries no delivery cost; shipping is reported as 0.00
  and totals are item price only.

Politeness: all requests go through PoliteSession (robots.txt honoured,
jittered per-host delay, exponential backoff). If robots.txt disallows the
endpoint we log once and return no results rather than fetch.
"""

from __future__ import annotations

import logging
from urllib.parse import urljoin

from ..http import PoliteSession, RobotsDisallowed
from ..models import ComparableListing

log = logging.getLogger(__name__)

BASE_URL = "https://www.pricerunner.com"
SEARCH_URL = f"{BASE_URL}/dk/api/search-compare-gateway/public/search/v5/UK"


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").lstrip("£"))
    except ValueError:
        return None


def _price_of(product: dict) -> float | None:
    """Pull the lowest price out of the couple of shapes the API uses."""
    for key in ("lowestPrice", "price", "minPrice"):
        node = product.get(key)
        if isinstance(node, dict):
            price = _to_float(node.get("amount") or node.get("value"))
        else:
            price = _to_float(node)
        if price is not None and price > 0:
            return price
    return None


def parse_search_response(payload: dict) -> list[ComparableListing]:
    listings: list[ComparableListing] = []
    for product in payload.get("products") or []:
        if not isinstance(product, dict):
            continue
        name = product.get("name") or product.get("title")
        price = _price_of(product)
        if not name or price is None:
            continue
        url = product.get("url") or ""
        if url and not url.startswith("http"):
            url = urljoin(BASE_URL, url)
        listings.append(
            ComparableListing(
                title=str(name),
                price=price,
                shipping=0.0,  # not present in the search response
                url=url,
                condition="New",
            )
        )
    return listings


class PriceRunnerClient:
    """Duck-type compatible with EbayBrowseClient.search()."""

    market_name = "pricerunner"
    default_min_listings = 1  # aggregated product rows, not individual listings

    def __init__(self, session: PoliteSession | None = None):
        self.session = session or PoliteSession()
        self._robots_warned = False

    def search(
        self,
        query: str | None = None,
        gtin: str | None = None,
        limit: int = 25,
        **_,
    ) -> list[ComparableListing]:
        """Search by free-text query or EAN (the endpoint accepts either as q)."""
        if not query and not gtin:
            raise ValueError("Provide query or gtin")
        q = gtin or query
        try:
            resp = self.session.get(
                SEARCH_URL,
                params={"q": q, "size": str(limit)},
                headers={"Accept": "application/json"},
            )
        except RobotsDisallowed:
            if not self._robots_warned:
                log.warning(
                    "pricerunner.com robots.txt disallows the search endpoint; "
                    "returning no results"
                )
                self._robots_warned = True
            return []
        if resp.status_code != 200:
            log.warning("PriceRunner search failed (%s): %s", resp.status_code, resp.text[:200])
            return []
        try:
            payload = resp.json()
        except ValueError:
            log.warning("PriceRunner returned non-JSON response (blocked?)")
            return []
        return parse_search_response(payload)[:limit]
