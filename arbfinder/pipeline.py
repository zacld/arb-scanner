"""Ties scraping, marketplace search, matching and pricing together."""

from __future__ import annotations

import logging

from .matching import clean_title, filter_matches, representative_price
from .models import Comparison, Product

log = logging.getLogger(__name__)


def compare_products(
    products: list[Product],
    ebay_client,
    min_score: float = 85.0,
    min_listings: int = 3,
) -> list[Comparison]:
    """Find an eBay comparable price for each product.

    Match by EAN when we have one (falling back to title search if the barcode
    finds nothing), otherwise by cleaned-title search + fuzzy filtering.
    Products with fewer than ``min_listings`` credible matches are skipped —
    one stray listing is not a market price.
    """
    comparisons: list[Comparison] = []
    for product in products:
        matched_by = None
        listings = []
        if product.ean:
            listings = filter_matches(
                product, ebay_client.search(gtin=product.ean), min_score, matched_by="ean"
            )
            if listings:
                matched_by = "ean"
        if not listings:
            query = clean_title(product.name)
            listings = filter_matches(
                product, ebay_client.search(query=query), min_score, matched_by="title"
            )
            matched_by = "title"
        if len(listings) < min_listings:
            log.info(
                "Skipping %r: only %d credible eBay match(es)", product.name, len(listings)
            )
            continue
        price, url = representative_price(listings)
        comparisons.append(
            Comparison(
                product=product,
                market="ebay",
                market_price=price,
                market_url=url,
                n_listings=len(listings),
                matched_by=matched_by,
                listings=listings,
            )
        )
    return comparisons
