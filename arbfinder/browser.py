"""Playwright browser fallback for pages that 403 plain HTTP clients.

Argos sits behind bot protection that rejects Python's requests client
outright while serving a real browser normally. This module fetches fully
rendered pages through Chromium, reusing one browser across fetches and
keeping the same politeness rules (jittered delay between navigations).

Optional dependency — install only if you hit 403s:

    pip install playwright
    playwright install chromium
"""

from __future__ import annotations

import logging
import random
import time
from pathlib import Path

log = logging.getLogger(__name__)

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

INSTALL_HINT = (
    "Playwright is not installed. To enable the browser fallback run:\n"
    "  pip install playwright\n"
    "  playwright install chromium"
)


class BrowserFetcher:
    """Lazily launched Chromium that fetches rendered HTML with polite delays."""

    def __init__(
        self,
        headless: bool = True,
        min_delay: float = 2.5,
        jitter: float = 0.75,
        timeout_s: float = 45.0,
        cdp_url: str | None = None,
    ):
        self.headless = headless
        self.min_delay = min_delay
        self.jitter = jitter
        self.timeout_s = timeout_s
        # When set, connect to an already-running Chrome over the DevTools
        # Protocol instead of launching one — reuses the user's real browser
        # session (and its bot-protection clearance) for autonomous scraping.
        self.cdp_url = cdp_url
        self._pw = None
        self._ctx = None
        self._page = None
        self._is_cdp = False
        self._last_nav = 0.0
        self.channel: str | None = None
        self.unavailable_reason: str | None = None

    def _ensure(self) -> bool:
        if self._page is not None:
            return True
        if self.unavailable_reason:
            return False
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            self.unavailable_reason = INSTALL_HINT
            log.warning(INSTALL_HINT)
            return False
        self._pw = sync_playwright().start()

        # -- connect to a user-launched Chrome over CDP ---------------------
        if self.cdp_url:
            try:
                browser = self._pw.chromium.connect_over_cdp(self.cdp_url)
                ctx = browser.contexts[0] if browser.contexts else browser.new_context()
                self._ctx = ctx
                # New tab in the existing context: shares its cookies (incl. the
                # Akamai clearance) without hijacking the tab you're viewing.
                self._page = ctx.new_page()
                self._is_cdp = True
                self.channel = "your Chrome (CDP)"
                log.info("Connected to your Chrome at %s", self.cdp_url)
                return True
            except Exception as exc:  # noqa: BLE001
                self.unavailable_reason = (
                    f"Could not connect to Chrome at {self.cdp_url}: {exc}\n"
                    "Launch Chrome with remote debugging first (see --via chrome help), "
                    "and browse the retailer once so it clears the bot check."
                )
                log.warning(self.unavailable_reason)
                self.close()
                return False
        # Prefer the user's REAL installed browser: Akamai fingerprints
        # Playwright's bundled test build specifically (a plain Safari/Chrome
        # visit from the same IP sails through), so real Chrome/Edge with a
        # persistent profile looks like the normal browsing that works. Each
        # channel gets its own profile dir so clearance cookies stick between
        # runs without cross-version profile clashes.
        last_exc: Exception | None = None
        for channel in ("chrome", "msedge", None):
            profile = Path.home() / f".arbfinder-profile-{channel or 'chromium'}"
            kwargs = dict(
                user_data_dir=str(profile),
                headless=self.headless,
                locale="en-GB",
                viewport={"width": 1366, "height": 768},
                args=["--disable-blink-features=AutomationControlled"],
            )
            if channel:
                kwargs["channel"] = channel  # native UA/fingerprint — don't override
            else:
                kwargs["user_agent"] = CHROME_UA
            try:
                profile.mkdir(parents=True, exist_ok=True)
                self._ctx = self._pw.chromium.launch_persistent_context(**kwargs)
                self._ctx.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                self._page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
                self.channel = channel or "chromium"
                log.info("Browser fallback using %s (profile: %s)",
                         self.channel, profile.name)
                return True
            except Exception as exc:  # noqa: BLE001 - try the next channel
                last_exc = exc
                self._ctx = self._page = None
                continue
        self.unavailable_reason = f"Could not launch any browser: {last_exc}"
        log.warning(self.unavailable_reason)
        self.close()
        return False

    def _throttle(self) -> None:
        if self._last_nav:
            wait = self.min_delay + random.uniform(-self.jitter, self.jitter)
            elapsed = time.monotonic() - self._last_nav
            if elapsed < wait:
                time.sleep(wait - elapsed)
        self._last_nav = time.monotonic()

    def fetch(self, url: str) -> str | None:
        """Return rendered page HTML, or None if the browser is unavailable
        or navigation failed (reason logged)."""
        if not self._ensure():
            return None
        self._throttle()
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_s * 1000)
            # Give client-side rendering time to fill the page in: wait for
            # network quiet if it comes, then a short settle either way.
            try:
                self._page.wait_for_load_state("networkidle", timeout=10_000)
            except Exception:  # noqa: BLE001 - busy pages never go idle; proceed anyway
                pass
            # Scroll through the page so virtualised/lazy product cards render,
            # then return to the top.
            try:
                self._page.evaluate(
                    """async () => {
                        const limit = Math.min(document.body.scrollHeight, 30000);
                        for (let y = 0; y < limit; y += 800) {
                            window.scrollTo(0, y);
                            await new Promise(r => setTimeout(r, 200));
                        }
                        window.scrollTo(0, 0);
                    }"""
                )
            except Exception:  # noqa: BLE001
                pass
            self._page.wait_for_timeout(1500)
            return self._page.content()
        except Exception as exc:  # noqa: BLE001
            log.warning("Browser fetch of %s failed: %s", url, exc)
            return None

    def close(self) -> None:
        closers = []
        if self._is_cdp:
            # Only close our own tab; leave the user's Chrome and its other
            # tabs running, and just detach the Playwright connection.
            closers.append(lambda: self._page.close() if self._page else None)
        elif self._ctx:
            closers.append(lambda: self._ctx.close())
        closers.append(lambda: self._pw.stop() if self._pw else None)
        for closer in closers:
            try:
                closer()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
        self._pw = self._ctx = self._page = None
        self._is_cdp = False
