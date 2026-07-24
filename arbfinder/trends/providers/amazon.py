"""Amazon Movers & Shakers / Best Sellers as a marketplace-demand signal.

Wraps the existing ``arbfinder.discover`` scraper. This is the strongest signal
in the mix — real products already gaining sales momentum — but it's still only
*candidate discovery*: profit/ROI validation decides if it's worth buying.
"""

from __future__ import annotations

from ..base import TrendSignal, TrendSignalProvider


class AmazonMoversProvider(TrendSignalProvider):
    name = "amazon_movers"
    enabled_by_default = True
    needs_browser = True

    def __init__(self, limit: int = 15):
        self.limit = limit

    def fetch(self, session=None, browser=None) -> list[TrendSignal]:
        if browser is None:
            return []
        from ...discover import discover_bestsellers
        ideas = discover_bestsellers(browser, limit=self.limit)
        out: list[TrendSignal] = []
        for i, idea in enumerate(ideas):
            # Rank position -> momentum: top of the list is hottest.
            strength = max(0.3, 1.0 - i * 0.05)
            out.append(TrendSignal(
                source="amazon_movers",
                original_title=idea.term,
                keywords=[idea.term],
                model=idea.term,          # kept as a specific-product lead
                trend_strength=round(strength, 3),
                market="UK",
            ))
        return out
