"""Core types for demand-guided discovery.

Two scores, deliberately kept apart:

* ``discovery_score`` on a ``TargetCategory`` decides which categories to *scan
  first* — pure "where to look", from trend/seasonal/marketplace signals.
* ``opportunity_score`` on an ``ArbitrageOpportunity`` (see arbfinder.opportunity)
  ranks *validated* products and is profit-first (65% net profit + ROI).

A trend never makes a low-profit item outrank a more profitable one.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class TrendSignal:
    """One raw demand signal from a provider (the standardised concept)."""

    source: str
    original_title: str
    product_category: str = ""     # filled by the classifier (or preset)
    brand: str | None = None
    model: str | None = None
    keywords: list[str] = field(default_factory=list)
    trend_strength: float = 0.5    # 0..1 popularity/momentum (non-seasonal)
    seasonal_strength: float = 0.0  # 0..1 position in an annual demand window
    market: str = "UK"
    timestamp: str = ""

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class TargetCategory:
    """A product category to investigate, with its discovery ranking."""

    term: str
    discovery_score: float
    sources: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    leads: list[str] = field(default_factory=list)          # specific models to also try
    seasonal_strength: float = 0.0
    trend_strength: float = 0.0                              # max non-seasonal strength


@dataclass
class ArbitrageOpportunity:
    """A validated, financially-scored opportunity (post price comparison)."""

    retailer_product: object
    marketplace_match: object
    buy_price: float
    expected_sale_price: float
    fees: float
    estimated_net_profit: float
    roi: float
    demand_confidence: float
    match_confidence: float
    trend_strength: float
    seasonal_strength: float
    opportunity_score: float

    # Secondary resale venue (the mismatch scan checks two: e.g. Amazon primary,
    # eBay secondary). These describe the OTHER venue for comparison/display only;
    # net/ROI/score above always derive from `marketplace_match` (the chosen one).
    secondary_market: str | None = None
    secondary_price: float | None = None
    secondary_url: str | None = None


class TrendSignalProvider(ABC):
    """A pluggable demand-signal source. Swap/remove providers independently."""

    name: str = "provider"
    enabled_by_default: bool = True
    needs_browser: bool = False

    @abstractmethod
    def fetch(self, session, browser=None) -> list[TrendSignal]:
        """Return raw signals (category may be blank; the engine classifies)."""
        raise NotImplementedError
