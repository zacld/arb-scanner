"""Dataclasses shared across the pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Product:
    """A product scraped from a source retail site."""

    name: str
    price: float
    url: str
    source: str = "argos"
    currency: str = "GBP"
    ean: str | None = None
    model_number: str | None = None
    brand: str | None = None


@dataclass
class ComparableListing:
    """A single listing found on a comparison marketplace."""

    title: str
    price: float
    shipping: float
    url: str
    condition: str = ""

    @property
    def total(self) -> float:
        return self.price + self.shipping


@dataclass
class Comparison:
    """A source product paired with its comparable marketplace price."""

    product: Product
    market: str  # e.g. "ebay"
    market_price: float  # representative (median) delivered price
    market_url: str
    n_listings: int
    matched_by: str  # "ean" or "title"
    listings: list[ComparableListing] = field(default_factory=list)

    @property
    def diff_abs(self) -> float:
        """Positive when the marketplace sells higher than the retail source."""
        return self.market_price - self.product.price

    @property
    def diff_pct(self) -> float:
        if self.product.price <= 0:
            return 0.0
        return self.diff_abs / self.product.price * 100.0
