"""Capture PriceRunner's real search API by driving a visible browser.

The search endpoint hardcoded in comparators/pricerunner.py was a blind guess
(it 404s). This probe reuses the installed Playwright browser: it opens a real
window, YOU do one search in it, and it records every JSON network response
that mentions products/prices — revealing the true endpoint URL and response
shape so the comparator can be pointed at the right thing.

Usage:
    python scripts/probe_pricerunner.py
    # a browser opens; in it, search for e.g. "ninja af400uk", let results
    # load, then return to this terminal and press Enter.

Then paste the printed list of URLs here, and share the largest
pricerunner_probe/resp_*.json file.
"""

from __future__ import annotations

import sys
from pathlib import Path

OUT = Path("pricerunner_probe")


def main() -> None:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright not installed. Run: pip install playwright && playwright install chromium")
        sys.exit(1)

    OUT.mkdir(exist_ok=True)
    start_url = sys.argv[1] if len(sys.argv) > 1 else "https://www.pricerunner.com/"
    captured: list[tuple[int, str, int]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(locale="en-GB", viewport={"width": 1366, "height": 800})
        page = ctx.new_page()

        def on_response(resp) -> None:
            try:
                ctype = resp.headers.get("content-type", "")
                if "json" not in ctype:
                    return
                body = resp.text()
                low = body.lower()
                if '"price"' not in low and "product" not in low:
                    return
                idx = len(captured) + 1
                (OUT / f"resp_{idx}.json").write_text(body, encoding="utf-8")
                (OUT / f"resp_{idx}.url.txt").write_text(resp.url, encoding="utf-8")
                captured.append((idx, resp.url, len(body)))
                print(f"[{idx}] {resp.status}  {resp.url[:130]}  ({len(body)} bytes)")
            except Exception:  # noqa: BLE001 - best-effort capture
                pass

        page.on("response", on_response)
        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=45000)
        except Exception as exc:  # noqa: BLE001
            print(f"(note: initial navigation warning: {exc})")

        print("\n" + "=" * 70)
        print("A browser window is open.")
        print("  1. Accept any cookie banner.")
        print("  2. Search for a product, e.g.  ninja af400uk  (or  ninja air fryer).")
        print("  3. Wait for the results to appear.")
        print("  4. Come back HERE and press Enter.")
        print("=" * 70)
        input("Press Enter when the results have loaded... ")
        browser.close()

    print(f"\nCaptured {len(captured)} candidate JSON response(s) into {OUT}\\")
    if captured:
        print("\nLargest responses (the search API is almost certainly the biggest):")
        for idx, url, n in sorted(captured, key=lambda c: -c[2])[:10]:
            print(f"  resp_{idx}.json  {n:>8} bytes  {url[:120]}")
        print(f"\nPaste this list here, and share the largest {OUT}\\resp_*.json file.")
    else:
        print("No product/price JSON was captured — the search may not have run, "
              "or results load differently. Tell me what you saw in the browser.")


if __name__ == "__main__":
    main()
