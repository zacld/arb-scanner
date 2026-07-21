"""Ties scraping, marketplace search, matching and pricing together."""

from __future__ import annotations

import logging

from .matching import clean_title, filter_matches, representative_price
from .models import Comparison, Product

log = logging.getLogger(__name__)


def compare_products(
    products: list[Product],
    client,
    min_score: float = 85.0,
    min_listings: int | None = None,
) -> list[Comparison]:
    """Find a marketplace comparable price for each product.

    ``client`` is any comparator exposing ``search(query=..., gtin=...)``
    (eBay, PriceRunner, or a demo stub). Match by EAN when we have one
    (falling back to title search if the barcode finds nothing), otherwise by
    cleaned-title search + fuzzy filtering. Products with fewer than
    ``min_listings`` credible matches are skipped — one stray listing is not
    a market price. When ``min_listings`` is None, the comparator's
    ``default_min_listings`` applies (3 for listing-level markets like eBay;
    1 for aggregated ones like PriceRunner).
    """
    market = getattr(client, "market_name", "ebay")
    if min_listings is None:
        min_listings = getattr(client, "default_min_listings", 3)
    comparisons: list[Comparison] = []
    for product in products:
        matched_by = None
        listings = []
        if product.ean:
            listings = filter_matches(
                product, client.search(gtin=product.ean), min_score, matched_by="ean"
            )
            if listings:
                matched_by = "ean"
        if not listings:
            query = clean_title(product.name)
            listings = filter_matches(
                product, client.search(query=query), min_score, matched_by="title"
            )
            matched_by = "title"
        if len(listings) < min_listings:
            log.info(
                "Skipping %r: only %d credible %s match(es)", product.name, len(listings), market
            )
            continue
        price, url = representative_price(listings)
        comparisons.append(
            Comparison(
                product=product,
                market=market,
                market_price=price,
                market_url=url,
                n_listings=len(listings),
                matched_by=matched_by,
                listings=listings,
            )
        )
    return comparisons
