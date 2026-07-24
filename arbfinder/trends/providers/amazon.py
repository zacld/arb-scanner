"""Amazon as a marketplace-demand signal — two independent sources.

* Movers & Shakers = biggest sales-rank risers (what's spiking now). Weighted
  highest in discovery; on by default.
* Best Sellers = steady-state top sellers across categories. Noisier for
  arbitrage (commodities, toiletries), so off by default and weighted lower.

Both wrap the existing ``arbfinder.discover`` scraper and only *discover*
candidates — profit/ROI validation still decides what's worth buying.
"""

from __future__ import annotations

from ..base import TrendSignal, TrendSignalProvider


def _to_signals(ideas, source: str) -> list[TrendSignal]:
    out: list[TrendSignal] = []
    for i, idea in enumerate(ideas):
        strength = max(0.3, 1.0 - i * 0.05)  # rank position -> momentum
        out.append(TrendSignal(
            source=source, original_title=idea.term, keywords=[idea.term],
            model=idea.term, trend_strength=round(strength, 3), market="UK"))
    return out


class AmazonMoversProvider(TrendSignalProvider):
    name = "amazon_movers"
    enabled_by_default = True
    needs_browser = True

    def __init__(self, limit: int = 15):
        self.limit = limit

    def fetch(self, session=None, browser=None) -> list[TrendSignal]:
        if browser is None:
            return []
        from ...discover import MOVERS_SOURCES, discover_bestsellers
        ideas = discover_bestsellers(browser, sources=MOVERS_SOURCES, limit=self.limit)
        return _to_signals(ideas, "amazon_movers")


class AmazonBestSellersProvider(TrendSignalProvider):
    name = "amazon_bestsellers"
    enabled_by_default = False  # noisier for arbitrage; opt in
    needs_browser = True

    def __init__(self, limit: int = 15):
        self.limit = limit

    def fetch(self, session=None, browser=None) -> list[TrendSignal]:
        if browser is None:
            return []
        from ...discover import BESTSELLER_SOURCES, discover_bestsellers
        ideas = discover_bestsellers(browser, sources=BESTSELLER_SOURCES, limit=self.limit)
        return _to_signals(ideas, "amazon_bestsellers")
