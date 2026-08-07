"""Launch — or detect — the user's real Chrome with a remote-debugging port.

The autonomous scrape (`--via chrome` / the dashboard's "My Chrome" mode) drives
a genuine, already-cleared Chrome over the DevTools Protocol to get past Akamai.
Previously the user had to start that Chrome by hand in a terminal; this module
does it for them, so the dashboard is truly point-and-click:

* if a Chrome is already listening on the CDP port, reuse it;
* otherwise find the installed Chrome and launch it with that port and a
  dedicated, persistent profile (so its bot-protection clearance sticks between
  runs), then wait for the port to come up.

The profile is separate from the user's normal Chrome, so their everyday windows
and cookies are untouched.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Dedicated profile — matches scripts/chrome-debug.command so the manual and
# automatic launchers share one cleared session.
DEBUG_PROFILE = Path.home() / ".arbfinder-chrome-debug"


def profile_looks_personal(profile_dir: Path = DEBUG_PROFILE) -> tuple[bool, str]:
    """Best-effort: has the throwaway scraping profile been contaminated?

    The scraping Chrome must never hold a real identity. If someone signs into
    Google in it, Sync can pull their extensions (e.g. a Phantom wallet) and
    logins in — which is how Gmail/a wallet can appear in a window automation is
    driving. We flag two tells on disk: installed extensions, or a signed-in
    account. Returns ``(looks_personal, human_reason)``; never raises.
    """
    default = profile_dir / "Default"
    try:
        ext_dir = default / "Extensions"
        if ext_dir.is_dir():
            installed = [p.name for p in ext_dir.iterdir()
                         if p.is_dir() and not p.name.startswith(".")]
            if installed:
                return True, f"{len(installed)} browser extension(s) installed"
    except OSError:
        pass
    try:
        data = json.loads((default / "Preferences").read_text(encoding="utf-8"))
        accounts = data.get("account_info") or []
        if accounts:
            email = accounts[0].get("email") or "a Google account"
            return True, f"signed into {email}"
        if data.get("google", {}).get("services", {}).get("last_account_id"):
            return True, "a Google account is signed in"
    except (OSError, ValueError):
        pass
    return False, ""

# Talk to the local DevTools port directly, never through an HTTP(S) proxy.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _port_from_url(cdp_url: str) -> int:
    return urlparse(cdp_url).port or 9222


def is_running(cdp_url: str = "http://127.0.0.1:9222", timeout: float = 1.0) -> bool:
    """True if a Chrome DevTools endpoint answers at ``cdp_url``."""
    try:
        with _opener.open(cdp_url.rstrip("/") + "/json/version", timeout=timeout) as resp:
            return resp.status == 200
    except Exception:  # noqa: BLE001 - any failure means "not up"
        return False


def _chrome_binary() -> str | None:
    """Best guess at the installed Chrome/Chromium executable, or None."""
    override = os.environ.get("ARBFINDER_CHROME")
    if override and Path(override).exists():
        return override
    candidates: list[str] = []
    if sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
        ]
    elif sys.platform.startswith("win"):
        candidates += [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser", "chrome"):
        found = shutil.which(name)
        if found:
            candidates.append(found)
    for path in candidates:
        if path and Path(path).exists():
            return path
    return None


def ensure_chrome(
    cdp_url: str = "http://127.0.0.1:9222",
    *,
    open_url: str | None = None,
    wait_s: float = 15.0,
) -> tuple[bool, str]:
    """Make sure a debuggable Chrome is up. Returns ``(running, message)``.

    Reuses an existing one on the port if present; otherwise launches the real
    installed Chrome with the debugging port and the dedicated profile, opening
    ``open_url`` first (so a first-ever visit warms/clears the bot check), and
    waits up to ``wait_s`` for the port to answer.
    """
    # SAFETY GUARD: never drive a contaminated scraping profile. If ours has been
    # signed in / had extensions installed, refuse (whether it's already running
    # or not) and tell the user to reset it — so a wallet/Gmail can't be surfaced
    # in the automated window again.
    personal, why = profile_looks_personal(DEBUG_PROFILE)
    if personal:
        return False, (
            f"Refusing to launch: the scraping Chrome profile has {why}. This must be a "
            "throwaway browser — a signed-in account or an extension (e.g. a wallet) has no "
            "place in the window the scraper drives. Reset it and try again:\n"
            "  pkill -f 'remote-debugging-port=9222'; rm -rf ~/.arbfinder-chrome-debug\n"
            "Then never sign into Google, email, or a wallet in that Chrome window."
        )

    if is_running(cdp_url):
        return True, "Reusing the Chrome already open on the debugging port."
    binary = _chrome_binary()
    if not binary:
        return False, (
            "Couldn't find Google Chrome to launch automatically. Install Chrome, "
            "set ARBFINDER_CHROME to its full path, or start it yourself with "
            "scripts/chrome-debug.command."
        )
    args = [
        binary,
        f"--remote-debugging-port={_port_from_url(cdp_url)}",
        f"--user-data-dir={DEBUG_PROFILE}",
        "--no-first-run",
        "--no-default-browser-check",
        # SAFETY: this is a scraping browser, never a personal one. Disable ALL
        # extensions so a wallet (e.g. Phantom) or anything else can't load or be
        # reached by a page we visit, and disable sync so a signed-in Google
        # account can't pull your extensions/logins into this profile. Never sign
        # into personal accounts or a wallet in this window.
        "--disable-extensions",
        "--disable-sync",
    ]
    if open_url:
        args.append(open_url)
    try:
        DEBUG_PROFILE.mkdir(parents=True, exist_ok=True)
        popen_kwargs: dict = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if not sys.platform.startswith("win"):
            popen_kwargs["start_new_session"] = True  # detach from our process group
        subprocess.Popen(args, **popen_kwargs)  # noqa: S603 - path is our own detection
    except Exception as exc:  # noqa: BLE001
        return False, f"Couldn't launch Chrome ({binary}): {exc}"
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if is_running(cdp_url):
            return True, "Launched a fresh Chrome for scraping."
        time.sleep(0.4)
    return False, (
        "Launched Chrome but its debugging port didn't come up in time — "
        "give it a moment and scan again."
    )
