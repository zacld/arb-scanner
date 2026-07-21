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

sys.path.insert(0, str(Path(__file__).parent.parent))
from arbfinder.sources.argos import (  # noqa: E402
    decode_flight_text,
    extract_flight_product_dicts,
    parse_search_page,
)

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

    print("\n== NEXT.JS FLIGHT DATA ==")
    import json as _json
    flight = decode_flight_text(html)
    print(f"decoded flight text: {len(flight)} chars; "
          f"'productData' occurrences: {flight.count(chr(34) + 'productData' + chr(34))}")
    raw = extract_flight_product_dicts(html)
    print(f"flight product dicts extracted: {len(raw)}")
    if raw:
        print("--- first product dict (truncated to 5000 chars) ---")
        print(_json.dumps(raw[0], indent=1)[:5000])

    print("\n== WHAT THE CURRENT PARSER GETS ==")
    products = parse_search_page(html)
    print(f"parse_search_page -> {len(products)} products")
    for p in products[:10]:
        print(f"  £{p.price:<9.2f} ean={p.ean or '-':<15} {p.name[:60]}")

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
