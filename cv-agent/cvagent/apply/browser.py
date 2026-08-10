"""Chromium launch with a fallback for preinstalled browsers.

Playwright resolves its browser by build number, so an environment that ships a
Chromium from a different Playwright release (a CI image, this repo's sandbox)
fails to launch even though a perfectly good binary is sitting on disk. When that
happens, point at the binary directly instead of downloading another copy.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

FALLBACK_PATHS = [
    os.environ.get("CV_AGENT_CHROMIUM", ""),
    "/opt/pw-browsers/chromium",
]


def launch_chromium(playwright: Any, *, headless: bool = True) -> Any:
    try:
        return playwright.chromium.launch(headless=headless)
    except Exception:
        for candidate in FALLBACK_PATHS:
            if candidate and Path(candidate).exists():
                return playwright.chromium.launch(
                    headless=headless, executable_path=candidate
                )
        raise
