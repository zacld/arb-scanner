"""Open a URL in a visible browser and dump the fully-rendered HTML.

General-purpose capture tool — the same "let a real browser render it, then
parse what a human sees" approach that cracked Argos, pointable at any site.
Use it to grab a comparison site's search-results page so the markup can be
inspected and a parser written.

Usage:
    python scripts/probe_page.py "https://www.google.com/search?tbm=shop&q=ninja+af400uk"
    # (no URL given -> defaults to a Google Shopping search for an air fryer)

A browser opens. Do whatever's needed (accept cookie/consent banners, let
results load), then return to the terminal and press Enter. The rendered page
is saved to probe_page.html and a quick signal summary is printed. Share
probe_page.html (or paste the summary) to have a parser built for it.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

DEFAULT_URL = "https://www.google.com/search?tbm=shop&q=ninja+foodi+af400uk+air+fryer"
OUT_HTML = Path("probe_page.html")


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    url = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_URL

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(locale="en-GB", viewport={"width": 1366, "height": 850})
        page = ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as exc:  # noqa: BLE001
            print(f"(navigation warning: {exc})")

        print("\n" + "=" * 70)
        print(f"Opened: {url}")
        print("  1. Accept any cookie / consent banner in the browser.")
        print("  2. Make sure product results with prices are visible.")
        print("  3. Come back HERE and press Enter to capture the page.")
        print("=" * 70)
        input("Press Enter when results are on screen... ")

        # Google Shopping fires background requests constantly, so page.content()
        # can hit "page is navigating" — settle, then retry a few times.
        html = None
        for attempt in range(6):
            try:
                page.wait_for_load_state("networkidle", timeout=4000)
            except Exception:  # noqa: BLE001 - never fully idle; that's fine
                pass
            try:
                html = page.content()
                break
            except Exception as exc:  # noqa: BLE001
                print(f"  (page busy, retrying {attempt + 1}/6…: {str(exc)[:60]})")
                page.wait_for_timeout(1500)
        if html is None:
            # Last resort: pull the DOM straight out of the renderer.
            try:
                html = page.evaluate("() => document.documentElement.outerHTML")
            except Exception as exc:  # noqa: BLE001
                print(f"Could not read page content: {exc}")
                browser.close()
                return
        OUT_HTML.write_text(html, encoding="utf-8")
        browser.close()

    # Quick signal summary so direction is clear even before sharing the file.
    title = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    print(f"\nSaved {len(html)} bytes to {OUT_HTML}")
    print(f"title: {title.group(1).strip() if title else '(none)'}")
    print(f"'£' price signs: {html.count(chr(163))}")
    for marker in ("consent", "captcha", "unusual traffic", "sorry/",
                   "data-docid", "sh-dgr", "product", '"price"'):
        n = len(re.findall(re.escape(marker), html, re.I))
        if n:
            print(f"  {marker!r}: {n}")
    print(f"\nShare {OUT_HTML}, or paste this summary, and I'll build the parser.")


if __name__ == "__main__":
    main()
