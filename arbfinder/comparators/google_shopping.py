"""Google Shopping comparator — no API key, driven through a real browser.

This is the "google the product, see what it costs elsewhere" comparator.
Google Shopping renders one offer tile per retailer for a product; each tile
carries aria-label="From <retailer>" and, in its text, the title, current
price, was-price and retailer. We parse by that aria-label rather than
Google's obfuscated, churning CSS class names, so the parser survives markup
changes.

Prices here are retailer *asks* (what you'd buy for), spanning Argos, Currys,
eBay, etc. — so like PriceRunner this is a retail comparison (resale_market =
False): a gap means "cheaper/dearer elsewhere", not resale profit.

Practical limits: Google shows a consent wall (handled by clicking "Accept
all") and may CAPTCHA automated searches. The client runs headed by default so
a CAPTCHA can be solved by hand, spaces requests out, and detects blocks and
degrades gracefully rather than looping.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path
from urllib.parse import quote_plus

from bs4 import BeautifulSoup

from ..models import ComparableListing

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.google.com/search?tbm=shop&hl=en-GB&gl=uk&q={q}"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Persist consent/verification between searches AND between runs, so a CAPTCHA
# solved once keeps the whole session (and later sessions) unblocked.
PROFILE_DIR = Path.home() / ".arbfinder-gprofile"
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

_FULL_PRICE = re.compile(r"^£\s?([\d,]+(?:\.\d{2})?)$")
_ANY_PRICE = re.compile(r"£\s?[\d,]+")
# Text fragments that are never part of a product title.
_NOISE = ("with code", " off", "tap and hold", "cancel", "continue", "expires",
          "free of charge", "free delivery", "in stock", "sponsored", "·")


def _looks_blocked(html: str) -> bool:
    low = html[:4000].lower()
    return ("unusual traffic" in low or "/sorry/" in low
            or "detected unusual" in low or "not a robot" in low)


def _clean_title_parts(parts: list[str]) -> list[str]:
    out = []
    for p in parts:
        pl = p.lower()
        if _ANY_PRICE.search(p) or pl.startswith("by ") or p.startswith("("):
            continue
        if any(n in pl for n in _NOISE) or len(p) < 2:
            continue
        out.append(p)
    return out


def parse_shopping_results(html: str) -> list[ComparableListing]:
    """Extract one ComparableListing per retailer offer from a rendered
    Google Shopping results page. De-duplicates the grid/list copies Google
    renders of the same offer."""
    soup = BeautifulSoup(html, "lxml")
    listings: list[ComparableListing] = []
    seen: set[tuple] = set()

    for el in soup.find_all(attrs={"aria-label": re.compile(r"^From\s+.")}):
        retailer = el.get("aria-label", "")[5:].strip()
        parts = [p.strip() for p in el.get_text("|", strip=True).split("|") if p.strip()]

        price = None
        price_idx = None
        for i, p in enumerate(parts):
            m = _FULL_PRICE.match(p)
            if m:
                val = float(m.group(1).replace(",", ""))
                if val >= 1:
                    price, price_idx = val, i
                    break
        if price is None:
            continue

        title_parts = _clean_title_parts(parts[:price_idx])
        title = " ".join(title_parts)[:120].strip()
        if not title:
            continue

        # The linked anchor may be the tile itself, a descendant, or an ancestor.
        link = None
        if el.name == "a" and el.get("href"):
            link = el
        else:
            link = el.find("a", href=True) or el.find_parent("a", href=True)
        url = link["href"] if link else ""
        if url.startswith("/"):
            url = "https://www.google.com" + url

        key = (title.lower()[:45], round(price, 2), retailer.lower())
        if key in seen:
            continue
        seen.add(key)
        listings.append(
            ComparableListing(title=title, price=price, shipping=0.0,
                              url=url, condition="", seller=retailer)
        )
    return listings


class GoogleShoppingClient:
    """Duck-type compatible with the other comparators' .search()."""

    market_name = "google"
    resale_market = False  # retailer asks, not resale value
    default_min_listings = 2  # a couple of retailers = a believable market

    def __init__(self, headless: bool = False, min_delay: float = 3.5, timeout_s: float = 45.0,
                 interactive: bool | None = None):
        self.headless = headless
        self.min_delay = min_delay
        self.timeout_s = timeout_s
        # Allow a manual CAPTCHA solve only when headed and attached to a TTY.
        self.interactive = (not headless and sys.stdin.isatty()) if interactive is None else interactive
        self._pw = None
        self._ctx = None
        self._page = None
        self._consented = False
        self._blocked = False
        self._prompted = False
        self._last = 0.0
        self.unavailable_reason: str | None = None

    # -- browser lifecycle --------------------------------------------------

    def _ensure(self) -> bool:
        if self._page is not None:
            return True
        if self.unavailable_reason:
            return False
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.unavailable_reason = (
                "Playwright not installed. Run: pip install playwright && "
                "playwright install chromium"
            )
            log.warning(self.unavailable_reason)
            return False
        try:
            self._pw = sync_playwright().start()
            # A persistent context keeps cookies/consent between searches and
            # runs; the stealth flag + init script hide the automation tell
            # that trips Google's "unusual traffic" wall.
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            self._ctx = self._pw.chromium.launch_persistent_context(
                user_data_dir=str(PROFILE_DIR),
                headless=self.headless,
                locale="en-GB",
                user_agent=CHROME_UA,
                viewport={"width": 1366, "height": 850},
                args=["--disable-blink-features=AutomationControlled"],
            )
            self._ctx.add_init_script(_STEALTH_JS)
            self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            return True
        except Exception as exc:  # noqa: BLE001
            self.unavailable_reason = f"Could not launch Chromium: {exc}"
            log.warning(self.unavailable_reason)
            self.close()
            return False

    def _accept_consent(self) -> None:
        if self._consented:
            return
        for name in ("Accept all", "I agree", "Accept the use", "Reject all"):
            try:
                btn = self._page.get_by_role("button", name=re.compile(name, re.I))
                if btn.count():
                    btn.first.click(timeout=4000)
                    self._page.wait_for_timeout(1200)
                    break
            except Exception:  # noqa: BLE001
                continue
        self._consented = True

    def _content(self) -> str | None:
        for _ in range(5):
            try:
                self._page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:  # noqa: BLE001
                pass
            try:
                return self._page.content()
            except Exception:  # noqa: BLE001 - page navigating; retry
                self._page.wait_for_timeout(1200)
        try:
            return self._page.evaluate("() => document.documentElement.outerHTML")
        except Exception:  # noqa: BLE001
            return None

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last
        if self._last and elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self._last = time.monotonic()

    # -- search -------------------------------------------------------------

    def search(self, query: str | None = None, gtin: str | None = None,
               limit: int = 25, **_) -> list[ComparableListing]:
        if not query:
            return []
        if self._blocked or not self._ensure():
            return []
        self._throttle()
        try:
            self._page.goto(SEARCH_URL.format(q=quote_plus(query)),
                            wait_until="domcontentloaded", timeout=self.timeout_s * 1000)
        except Exception as exc:  # noqa: BLE001
            log.warning("Google Shopping navigation failed for %r: %s", query, exc)
            return []
        self._accept_consent()
        html = self._content()
        if html is None:
            return []

        if html is not None and _looks_blocked(html):
            # Let the user solve it by hand, re-checking after each Enter so an
            # early press isn't fatal. Once cleared, the persistent profile
            # keeps the session unblocked for the rest of the run.
            attempts = 0
            while self.interactive and html is not None and _looks_blocked(html) and attempts < 5:
                attempts += 1
                print(
                    "\n>>> Google is showing a CAPTCHA / 'unusual traffic' check.\n"
                    ">>> In the browser window: solve it (tick the box / pick images),\n"
                    ">>> and WAIT until you actually SEE air-fryer results on screen.\n"
                    ">>> THEN come back here and press Enter (or type 'skip' to give up).",
                    file=sys.stderr,
                )
                try:
                    ans = input()
                except EOFError:
                    break
                if ans.strip().lower() == "skip":
                    break
                html = self._content()
            if html is None or _looks_blocked(html):
                self._blocked = True
                log.warning(
                    "Google still blocked; stopping Google lookups for this run. "
                    "Try again in a minute, or use --comparator ebay."
                )
                return []
        results = parse_shopping_results(html)
        for r in results:
            if not r.url:
                r.url = SEARCH_URL.format(q=quote_plus(query))
        return results[:limit]

    def close(self) -> None:
        for closer in (lambda: self._ctx and self._ctx.close(),
                       lambda: self._pw and self._pw.stop()):
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
        self._pw = self._ctx = self._page = None
