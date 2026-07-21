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
    ):
        self.headless = headless
        self.min_delay = min_delay
        self.jitter = jitter
        self.timeout_s = timeout_s
        self._pw = None
        self._browser = None
        self._page = None
        self._last_nav = 0.0
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
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=self.headless)
            context = self._browser.new_context(
                user_agent=CHROME_UA,
                locale="en-GB",
                viewport={"width": 1366, "height": 768},
            )
            self._page = context.new_page()
            return True
        except Exception as exc:  # noqa: BLE001 - launch failures should degrade, not crash
            self.unavailable_reason = f"Could not launch Chromium: {exc}"
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
            # Give client-side rendering a moment to fill the page in.
            self._page.wait_for_timeout(1500)
            return self._page.content()
        except Exception as exc:  # noqa: BLE001
            log.warning("Browser fetch of %s failed: %s", url, exc)
            return None

    def close(self) -> None:
        for closer in (
            lambda: self._browser.close() if self._browser else None,
            lambda: self._pw.stop() if self._pw else None,
        ):
            try:
                closer()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass
        self._pw = self._browser = self._page = None
