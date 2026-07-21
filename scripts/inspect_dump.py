"""Print a compact structural summary of a saved Argos page dump.

Usage (from the repo root, after a failed scan saved debug_argos_page.html):

    python scripts/inspect_dump.py

Paste the full output into the chat/issue — it contains everything needed
to adapt the parser to the current markup, without sharing the whole 2MB file.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "debug_argos_page.html")
MAX_CARD_CHARS = 6000


def main() -> None:
    html = PATH.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")

    product_links = soup.select('a[href*="/product/"]')
    print(f"== FILE == {PATH} ({len(html)} bytes)")
    print(f"pound signs: {html.count('£')}   product links: {len(product_links)}")

    print("\n== TOP data-test VALUES ==")
    counts = Counter(re.findall(r'data-test="([^"]+)"', html))
    for value, n in counts.most_common(40):
        print(f"{n:4d}  {value}")

    print("\n== BIG SCRIPT TAGS (candidates for embedded JSON) ==")
    scripts = sorted(soup.find_all("script"), key=lambda s: -len(s.string or ""))
    for s in scripts[:10]:
        body = s.string or ""
        if len(body) < 5000:
            continue
        attrs = {k: v for k, v in s.attrs.items()}
        print(f"--- script {attrs} len={len(body)}")
        print(body[:300].replace("\n", " "))
        print("...")

    print("\n== FIRST 10 PRODUCT LINK HREFS ==")
    for a in product_links[:10]:
        print(a.get("href"))

    print("\n== SAMPLE PRODUCT CARD (first product link, climbing to its card) ==")
    if not product_links:
        print("(no /product/ links found)")
        return
    node = product_links[0]
    for _ in range(8):
        if node.parent is None:
            break
        if "£" in node.get_text():
            break
        node = node.parent
    markup = str(node)
    print(markup[:MAX_CARD_CHARS])
    if len(markup) > MAX_CARD_CHARS:
        print(f"... (truncated, {len(markup)} chars total)")


if __name__ == "__main__":
    main()
