"""Offline demo: runs the real pipeline over bundled fixtures.

The Argos fixture mirrors the live page's embedded ``window.App`` JSON and is
parsed by the *same* ``parse_search_page`` used for live scans; the eBay
fixture holds canned Browse API responses fed through the same
``parse_item_summaries``. Only the network transport is stubbed.
"""

from __future__ import annotations

import json
from pathlib import Path

from rapidfuzz import fuzz

from .comparators.ebay import parse_item_summaries
from .models import ComparableListing, Product
from .sources.argos import parse_search_page

DATA_DIR = Path(__file__).parent / "demo_data"


def demo_products() -> list[Product]:
    html = (DATA_DIR / "argos_search.html").read_text(encoding="utf-8")
    return parse_search_page(html)


class DemoEbayClient:
    """Duck-typed stand-in for EbayBrowseClient backed by fixture responses."""

    def __init__(self, data: dict | None = None):
        if data is None:
            data = json.loads((DATA_DIR / "ebay_responses.json").read_text(encoding="utf-8"))
        self.data = data

    def search(self, query: str | None = None, gtin: str | None = None, **_) -> list[ComparableListing]:
        if gtin:
            payload = self.data.get("by_gtin", {}).get(gtin, {})
            return parse_item_summaries(payload)
        by_query = self.data.get("by_query", {})
        if not query or not by_query:
            return []
        best_key = max(by_query, key=lambda k: fuzz.token_set_ratio(k, query))
        if fuzz.token_set_ratio(best_key, query) < 60:
            return []
        return parse_item_summaries(by_query[best_key])
