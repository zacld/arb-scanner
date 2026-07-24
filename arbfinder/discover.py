"""Demand radar: discover what's selling *right now* to hunt at retail.

Instead of you typing search terms, this reads live "what's popular" signals —
Amazon's **Movers & Shakers** (biggest sales-rank risers = trending now) and
**Best Sellers** by category — and turns them into candidate product terms. The
hunt then searches those at a retail source (Argos) and checks the resale margin,
so the tool surfaces *profitable* popular items on its own.

It's read through the same real browser the scraper uses (best with your Chrome
over CDP), because Amazon fingerprints automation. No API key.

Markup churn: Amazon's grid class names are obfuscated and change, so the parser
keys off stable structure — product links (`/dp/<ASIN>`) and a nearby £ price —
rather than brittle CSS classes.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

# Movers & Shakers is ranked by sales-rank *change* — what's spiking right now
# (seasonal/event demand). Best Sellers is steady-state top sellers (noisier for
# arbitrage). Kept as separate lists so the two can be toggled independently.
MOVERS_SOURCES: list[tuple[str, str]] = [
    ("Movers & Shakers", "https://www.amazon.co.uk/gp/movers-and-shakers"),
]
BESTSELLER_SOURCES: list[tuple[str, str]] = [
    ("Best Sellers", "https://www.amazon.co.uk/gp/bestsellers"),
    ("Home & Kitchen", "https://www.amazon.co.uk/gp/bestsellers/kitchen"),
    ("Garden", "https://www.amazon.co.uk/gp/bestsellers/garden"),
    ("Electronics", "https://www.amazon.co.uk/gp/bestsellers/electronics"),
]
DEFAULT_SOURCES = MOVERS_SOURCES + BESTSELLER_SOURCES

_ASIN = re.compile(r"/dp/([A-Z0-9]{10})")
_PRICE = re.compile(r"£\s?([\d,]+\.\d{2})")


@dataclass
class Idea:
    """A candidate product to hunt, discovered from a demand signal."""

    term: str          # search term to run at the retail source
    reason: str        # where it came from, e.g. "Movers & Shakers"
    amazon_price: float | None = None  # its price on the demand page (a resale ref)
    url: str = ""      # the Amazon product URL it came from


def _short_term(title: str) -> str:
    """Trim a long Amazon title to a searchable brand+model-ish term."""
    t = re.split(r"[,|(]", title, 1)[0]
    t = re.sub(r"\s+", " ", t).strip()
    return t[:60].strip()


def parse_bestsellers(html: str, limit: int = 20, reason: str = "Amazon best seller") -> list[Idea]:
    """Extract candidate Ideas from an Amazon best-sellers / movers page.

    Keys off `/dp/<ASIN>` product links (deduped by ASIN) and the nearest £
    price, so it survives Amazon's churning CSS class names.
    """
    soup = BeautifulSoup(html, "lxml")
    # Prefer the personalisation grid (best-seller faceouts carry a 'p13n'
    # class prefix that's stable even as the suffix churns); fall back to page.
    scope = soup.select('[class*="p13n"]') or [soup]

    by_asin: dict[str, dict] = {}
    order: list[str] = []
    for root in scope:
        for a in root.select('a[href*="/dp/"]'):
            m = _ASIN.search(a.get("href", ""))
            if not m:
                continue
            asin = m.group(1)
            title = a.get_text(" ", strip=True)
            if len(title) < 8:
                img = a.find("img")
                title = (img.get("alt", "").strip() if img and img.get("alt") else "")
            rec = by_asin.get(asin)
            if rec is None:
                rec = {"title": "", "price": None}
                by_asin[asin] = rec
                order.append(asin)
            if len(title) > len(rec["title"]):
                rec["title"] = title
            if rec["price"] is None:
                # Search for a price only WITHIN this item's own card (the
                # stable gridItemRoot / faceout ancestor), so we don't grab a
                # neighbouring item's price from the shared grid.
                card = a
                for _ in range(5):
                    card = card.parent
                    if card is None:
                        break
                    cls = " ".join(card.get("class") or [])
                    if card.get("id") == "gridItemRoot" or "faceout" in cls:
                        pm = _PRICE.search(card.get_text(" ", strip=True))
                        if pm:
                            rec["price"] = float(pm.group(1).replace(",", ""))
                        break

    ideas: list[Idea] = []
    for asin in order:
        rec = by_asin[asin]
        if len(rec["title"]) < 8:
            continue
        ideas.append(Idea(
            term=_short_term(rec["title"]),
            reason=reason,
            amazon_price=rec["price"],
            url=f"https://www.amazon.co.uk/dp/{asin}",
        ))
        if len(ideas) >= limit:
            break
    return ideas


def discover_bestsellers(browser, sources=None, limit: int = 12,
                         per_source: int = 10) -> list[Idea]:
    """Fetch each demand page through ``browser`` and merge to <= ``limit``
    unique Ideas. ``browser`` is a BrowserFetcher (ideally CDP-backed)."""
    sources = sources or DEFAULT_SOURCES
    ideas: list[Idea] = []
    seen: set[str] = set()
    for label, url in sources:
        if len(ideas) >= limit:
            break
        log.info("Discovering from %s (%s)", label, url)
        html = browser.fetch(url)
        if not html:
            log.warning("No page returned for %s", label)
            continue
        for idea in parse_bestsellers(html, limit=per_source, reason=label):
            key = idea.term.lower()
            if not key or key in seen:
                continue
            seen.add(key)
            ideas.append(idea)
            if len(ideas) >= limit:
                break
    return ideas
