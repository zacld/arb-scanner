"""Amazon UK comparator — no API key, driven through a real browser.

The "sell it on Amazon instead" comparator. Amazon has no free open pricing
API (unlike eBay's Browse), so this **prototype** reads the public search
results the way a person would: through a genuine Chrome. It reuses the same
CDP-to-your-real-Chrome trick that gets past Argos's Akamai — Amazon fingerprints
automation hard, and a real, already-warmed browser is the reliable way in.

Amazon is a *resale* venue here: the price shown is what the item sells for on
Amazon, so a gap over the retail buy price is genuine resale margin (``resale_market
= True``) — net profit is computed off it, same as eBay. What this prototype does
NOT have yet is **sales rank / demand** (a great price gap is worthless if the
item never sells) or **exact FBA fees** — those need Keepa or Amazon's SP-API,
the paid step you take once this proves gaps exist.

Limits: current price only, no BSR; Amazon may show a CAPTCHA on automated
access (solvable by hand in the visible window, or dodged entirely by reusing
your cleared Chrome); markup changes can need selector tweaks.
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

SEARCH_URL = "https://www.amazon.co.uk/s?k={q}"
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
# Own persistent profile for the no-CDP fallback, so a solved CAPTCHA sticks.
PROFILE_DIR = Path.home() / ".arbfinder-amazon-profile"
_STEALTH_JS = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"

_PRICE = re.compile(r"[\d,]+\.\d{2}")


def _looks_blocked(html: str) -> bool:
    low = html[:6000].lower()
    return (
        "not a robot" in low
        or "enter the characters you see" in low
        or "type the characters" in low
        or "/errors/validatecaptcha" in low
        or "sorry, we just need to make sure" in low
        or "automated access to amazon data" in low
    )


def parse_amazon_results(html: str) -> list[ComparableListing]:
    """Extract one ComparableListing per organic Amazon search result that
    carries a price. De-duplicates by ASIN."""
    soup = BeautifulSoup(html, "lxml")
    listings: list[ComparableListing] = []
    seen: set[str] = set()

    for res in soup.select('div[data-component-type="s-search-result"]'):
        asin = (res.get("data-asin") or "").strip()
        if not asin or asin in seen:
            continue

        # Price: the current offer's a-offscreen text (e.g. "£49.99"). Skip
        # results with no buyable price (out of stock, "see options", etc.).
        price_el = res.select_one("span.a-price span.a-offscreen")
        if not price_el:
            continue
        m = _PRICE.search(price_el.get_text())
        if not m:
            continue
        price = float(m.group().replace(",", ""))
        if price < 1:
            continue

        h2 = res.select_one("h2")
        title = h2.get_text(" ", strip=True) if h2 else ""
        if not title:
            # Newer markup keeps the title in the link's aria-label.
            link = res.select_one("a.a-link-normal[aria-label]")
            title = link.get("aria-label", "").strip() if link else ""
        if not title:
            continue

        seen.add(asin)
        listings.append(
            ComparableListing(
                title=title[:140],
                price=price,
                shipping=0.0,  # Amazon shows delivered/Prime price; treat as total
                url=f"https://www.amazon.co.uk/dp/{asin}",
                condition="",
                seller="Amazon",
            )
        )
    return listings


class AmazonClient:
    """Duck-type compatible with the other comparators' .search().

    Prefers to drive your already-open Chrome over CDP (reuses its real session
    /clearance); falls back to launching its own persistent, stealthed browser.
    """

    market_name = "amazon"
    resale_market = True  # Amazon price = what it sells for → real resale margin
    default_min_listings = 1  # Amazon has one canonical listing per product

    def __init__(self, cdp_url: str | None = None, headless: bool = False,
                 min_delay: float = 3.0, timeout_s: float = 45.0,
                 interactive: bool | None = None):
        self.cdp_url = cdp_url
        self.headless = headless
        self.min_delay = min_delay
        self.timeout_s = timeout_s
        self.interactive = (
            (not headless and sys.stdin.isatty()) if interactive is None else interactive
        )
        self._pw = None
        self._ctx = None
        self._page = None
        self._is_cdp = False
        self._blocked = False
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
        self._pw = sync_playwright().start()

        # Reuse the user's real Chrome over CDP when we can — same clearance
        # trick as the source scraper.
        if self.cdp_url:
            try:
                browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                self._ctx = ctx
                self._page = ctx.new_page()
                self._is_cdp = True
                log.info("Amazon comparator using your Chrome at %s", self.cdp_url)
                return True
            except Exception as exc:  # noqa: BLE001 - fall back to own browser
                log.info("Amazon: couldn't attach to your Chrome (%s); launching own browser", exc)

        try:
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
            self.unavailable_reason = f"Could not launch a browser for Amazon: {exc}"
            log.warning(self.unavailable_reason)
            self.close()
            return False

    def _content(self) -> str | None:
        for _ in range(5):
            try:
                self._page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:  # noqa: BLE001
                pass
            try:
                return self._page.content()
            except Exception:  # noqa: BLE001 - page navigating; retry
                self._page.wait_for_timeout(1000)
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
               limit: int = 20, **_) -> list[ComparableListing]:
        # Amazon search by keyword; a GTIN is just used as the query term.
        term = query or gtin
        if not term:
            return []
        if self._blocked or not self._ensure():
            return []
        self._throttle()
        try:
            self._page.goto(SEARCH_URL.format(q=quote_plus(term)),
                            wait_until="domcontentloaded", timeout=self.timeout_s * 1000)
        except Exception as exc:  # noqa: BLE001
            log.warning("Amazon navigation failed for %r: %s", term, exc)
            return []
        html = self._content()
        if html is None:
            return []

        if _looks_blocked(html):
            attempts = 0
            while self.interactive and html is not None and _looks_blocked(html) and attempts < 5:
                attempts += 1
                print(
                    "\n>>> Amazon is showing a CAPTCHA / robot check.\n"
                    ">>> In the browser window: solve it and WAIT until you SEE search results,\n"
                    ">>> then come back here and press Enter (or type 'skip' to give up).",
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
                    "Amazon still blocked; stopping Amazon lookups for this run. "
                    "Reuse your real Chrome (--via chrome) or try again shortly."
                )
                return []

        results = parse_amazon_results(html)
        return results[:limit]

    def close(self) -> None:
        closers = []
        if self._is_cdp:
            # Never close the user's Chrome — just our own tab + the connection.
            closers.append(lambda: self._page.close() if self._page else None)
        elif self._ctx:
            closers.append(lambda: self._ctx.close())
        closers.append(lambda: self._pw.stop() if self._pw else None)
        for closer in closers:
            try:
                closer()
            except Exception:  # noqa: BLE001
                pass
        self._pw = self._ctx = self._page = None
        self._is_cdp = False
