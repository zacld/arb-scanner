"""Price providers + their cost, so the funnel can spend browser/paid effort
sparingly.

Every venue already duck-types the comparator interface —
``search(query=..., gtin=...) -> list[ComparableListing]`` plus ``market_name``,
``resale_market`` and ``default_min_listings``. This module just tags each with a
**cost** and offers factories, so the two-stage funnel can order them
"cheap-first" and a paid API (e.g. Keepa) is a genuine one-file drop-in — nothing
in the funnel, scoring or UI changes when you add one.
"""

from __future__ import annotations

# Cost tiers the funnel orders by: free structured APIs run wide, browser and
# paid providers only touch the survivors.
COST_API_FREE = "api-free"   # eBay Browse API — free, fast, no browser
COST_BROWSER = "browser"     # Amazon/Argos — free but a real-Chrome page load each
COST_API_PAID = "api-paid"   # Keepa etc. — costs money, so treat like "expensive"


def ebay_provider(client_id: str, secret: str, env: str = "PRODUCTION"):
    """eBay Browse API resale comparator (the cheap wide-pass pricer)."""
    from .comparators.ebay import EbayBrowseClient
    client = EbayBrowseClient(client_id, secret, env=env)
    client.cost = COST_API_FREE
    return client


def amazon_provider(cdp_url: str | None = None):
    """Amazon resale comparator via a real Chrome (the expensive enrich pricer)."""
    from .comparators.amazon import AmazonClient
    client = AmazonClient(cdp_url=cdp_url, headless=False, interactive=False)
    client.cost = COST_BROWSER
    return client


class KeepaAmazonClient:
    """PAID DROP-IN SLOT — Amazon price **plus real sales-rank/velocity** via the
    Keepa API (~£16/mo). Left unimplemented on purpose: no cost until you enable
    it, and adding it changes nothing else because it speaks the same comparator
    interface as ``AmazonClient``.

    To enable (see docs/providers.md):
      * take a Keepa API key,
      * in ``search()`` call Keepa's product endpoint (by title or, better, the
        Argos EAN via ``gtin``), returning ``ComparableListing`` objects from the
        current Amazon price, and derive demand from the sales-rank drop count,
      * then use ``keepa_provider(key)`` in place of ``amazon_provider()`` for the
        rich stage — the funnel and dashboard need no other change.
    """

    market_name = "amazon"
    resale_market = True
    default_min_listings = 1
    cost = COST_API_PAID

    def __init__(self, api_key: str):
        self.api_key = api_key

    def search(self, query: str | None = None, gtin: str | None = None, **_):
        raise NotImplementedError(
            "Keepa provider is the paid drop-in slot and isn't implemented yet — "
            "see docs/providers.md. Until then the free Amazon browser provider is used."
        )

    def close(self) -> None:  # symmetry with the browser providers
        pass


def keepa_provider(api_key: str) -> KeepaAmazonClient:
    return KeepaAmazonClient(api_key)
