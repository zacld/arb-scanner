"""Fuse trend signals into a ranked list of categories to investigate.

Output ordering is ``discovery_score`` — purely "where to look first". It never
ranks final opportunities; profit/ROI does that (see arbfinder.opportunity).
"""

from __future__ import annotations

import logging
from collections import defaultdict

from .base import TargetCategory, TrendSignalProvider
from .cache import DEFAULT_TTL, cached_signals
from .normalize import make_rules_classifier

log = logging.getLogger(__name__)

# Discovery weighting only (scan order). Amazon = real demand, manual = you
# asked for it, so they lead; seasonal contributes via its own term.
SOURCE_WEIGHT = {
    "amazon_movers": 1.5,       # spiking demand — highest
    "manual": 1.3,
    "tiktok_trending": 1.0,
    "seasonal": 1.0,
    "amazon_bestsellers": 0.8,  # steady-state, noisier — below movers
}
_SEASON_W = 1.0
# Sources whose products are real even if the lexicon doesn't recognise them.
_KEEP_UNMAPPED = frozenset(
    {"amazon_movers", "amazon_bestsellers", "manual", "tiktok_trending"})


class TrendEngine:
    def __init__(self, providers: list[TrendSignalProvider], classifier=None,
                 cache_ttl: float = DEFAULT_TTL):
        self.providers = providers
        self.classify = classifier or make_rules_classifier(_KEEP_UNMAPPED)
        self.cache_ttl = cache_ttl

    def discover(self, session=None, browser=None, limit: int = 12,
                 fresh: bool = False) -> list[TargetCategory]:
        groups = defaultdict(list)
        for provider in self.providers:
            try:
                signals = cached_signals(
                    provider.name, self.cache_ttl,
                    lambda p=provider: p.fetch(session, browser), fresh=fresh)
            except Exception as exc:  # noqa: BLE001 - one bad source can't sink discovery
                log.warning("Trend provider %r failed: %s", provider.name, exc)
                continue
            for sig in signals:
                category = self.classify(sig)
                if not category:
                    continue
                sig.product_category = category
                groups[category].append(sig)

        targets: list[TargetCategory] = []
        for category, sigs in groups.items():
            distinct = {s.source for s in sigs}
            non_seasonal = [s for s in sigs if s.source != "seasonal"]
            trend_evidence = sum(
                SOURCE_WEIGHT.get(s.source, 1.0) * s.trend_strength for s in non_seasonal)
            seasonal_strength = max((s.seasonal_strength for s in sigs), default=0.0)
            trend_strength = max((s.trend_strength for s in non_seasonal), default=0.0)
            agreement = 1.0 + 0.3 * (len(distinct) - 1)
            score = (trend_evidence + seasonal_strength * _SEASON_W) * agreement
            leads = []
            for s in sigs:
                if s.source.startswith("amazon") and (s.model or s.original_title):
                    lead = s.model or s.original_title
                    if lead not in leads:
                        leads.append(lead)
            reasons = [f"{s.source}: {s.original_title[:50]}" for s in sigs[:4]]
            targets.append(TargetCategory(
                term=category, discovery_score=round(score, 3),
                sources=sorted(distinct), reasons=reasons, leads=leads[:3],
                seasonal_strength=round(seasonal_strength, 3),
                trend_strength=round(trend_strength, 3),
            ))
        targets.sort(key=lambda t: t.discovery_score, reverse=True)
        return targets[:limit]


def build_providers(names, *, manual_terms=None, amazon_limit=15,
                    today=None, tiktok_url=None) -> list[TrendSignalProvider]:
    """Instantiate providers by name (unknown names are skipped)."""
    from .providers.amazon import AmazonBestSellersProvider, AmazonMoversProvider
    from .providers.manual import ManualProvider
    from .providers.seasons import SeasonalProvider
    from .providers.tiktok import TikTokShopProvider

    factories = {
        "movers": lambda: AmazonMoversProvider(limit=amazon_limit),
        "amazon": lambda: AmazonMoversProvider(limit=amazon_limit),  # alias for movers
        "bestsellers": lambda: AmazonBestSellersProvider(limit=amazon_limit),
        "seasonal": lambda: SeasonalProvider(today=today),
        "manual": lambda: ManualProvider(terms=manual_terms),
        "tiktok": lambda: TikTokShopProvider(url=tiktok_url),
    }
    providers = []
    for name in names:
        factory = factories.get(name)
        if factory:
            providers.append(factory())
        else:
            log.warning("Unknown trend provider %r — skipping", name)
    return providers
