"""Trend-driven target selection: decide WHAT to hunt from live signals.

Stage 1 of the hunt pipeline. Several cheap, scrapeable `TrendSignalProvider`s
(Amazon movers, Google Trends, Reddit, weather, X, manual, …) emit standardised
`TrendSignal`s; the `TrendEngine` normalises them into product categories and
ranks by cross-source agreement. The ranked category terms then feed the
existing scrape → compare → net-profit validation (stage 2) — trend signals only
decide what to investigate, never whether it's profitable.
"""

from .base import TargetCategory, TrendSignal, TrendSignalProvider
from .engine import TrendEngine

__all__ = ["TrendSignal", "TargetCategory", "TrendSignalProvider", "TrendEngine"]
