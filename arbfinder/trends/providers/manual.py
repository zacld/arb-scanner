"""Manual trend provider: categories you've spotted yourself.

The reliable fallback — you're the best trend-spotter. Supplied via the CLI
(`--trend-term`) or the dashboard box. Always trusted (kept even if the lexicon
doesn't recognise it).
"""

from __future__ import annotations

from ..base import TrendSignal, TrendSignalProvider


class ManualProvider(TrendSignalProvider):
    name = "manual"
    enabled_by_default = True
    needs_browser = False

    def __init__(self, terms: list[str] | None = None):
        self.terms = [t.strip() for t in (terms or []) if t.strip()]

    def fetch(self, session=None, browser=None) -> list[TrendSignal]:
        return [TrendSignal(source="manual", original_title=t, keywords=[t],
                            trend_strength=1.0, market="UK") for t in self.terms]
