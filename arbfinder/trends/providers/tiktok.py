"""TikTok Shop trend provider — best-effort, OFF by default.

There's no reliably-free, no-login TikTok Shop trending feed (the good
aggregators — Kalodata, FastMoss, Shoplus, EchoTik — gate their data). So this
provider is experimental: point it at a public aggregator page via
``ARBFINDER_TIKTOK_URL`` and it scrapes visible product-name text through the
browser. Behind the same provider interface so it's trivially swappable, and the
manual box is the dependable stand-in until a stable public source is found.
"""

from __future__ import annotations

import logging
import os
import re

from bs4 import BeautifulSoup

from ..base import TrendSignal, TrendSignalProvider

log = logging.getLogger(__name__)

_JUNK = re.compile(r"(sign in|log in|cookie|subscribe|pricing|©|\bapi\b)", re.I)


class TikTokShopProvider(TrendSignalProvider):
    name = "tiktok_trending"
    enabled_by_default = False  # experimental; enable explicitly
    needs_browser = True

    def __init__(self, url: str | None = None, limit: int = 15):
        self.url = url or os.environ.get("ARBFINDER_TIKTOK_URL", "")
        self.limit = limit

    def fetch(self, session=None, browser=None) -> list[TrendSignal]:
        if not self.url or browser is None:
            if not self.url:
                log.info("TikTok provider: set ARBFINDER_TIKTOK_URL to a public "
                         "aggregator page to enable it.")
            return []
        html = browser.fetch(self.url)
        if not html:
            return []
        soup = BeautifulSoup(html, "lxml")
        out: list[TrendSignal] = []
        seen: set[str] = set()
        # Generic: product-ish text is usually in card headings/links. Grab
        # short, non-junk phrases with letters — dial in per source later.
        for el in soup.select("a, h3, h4, [class*='title'], [class*='product']"):
            text = el.get_text(" ", strip=True)
            if not (8 <= len(text) <= 80) or _JUNK.search(text):
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(TrendSignal(source="tiktok_trending", original_title=text,
                                   keywords=[text], trend_strength=0.6, market="US"))
            if len(out) >= self.limit:
                break
        return out
