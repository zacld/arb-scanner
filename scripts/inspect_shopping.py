"""Summarise a saved Google Shopping page: find product tiles and show how
title / price / retailer are laid out, so a parser can target them.

Usage (after probe_page.py saved probe_page.html):
    python scripts/inspect_shopping.py

Paste the whole output here to have the Google Shopping comparator built.
"""

from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

PATH = Path(sys.argv[1] if len(sys.argv) > 1 else "probe_page.html")
PRICE_RE = re.compile(r"£\s?\d[\d,]*(?:\.\d{2})?")


def tile_of(node):
    """Climb from a price text node to the smallest ancestor that looks like a
    product tile: contains a price and a bounded amount of text (title +
    retailer, not the whole page)."""
    el = node.parent
    for _ in range(14):
        if el is None:
            break
        txt = el.get_text(" ", strip=True)
        if PRICE_RE.search(txt) and 20 < len(txt) < 500:
            return el
        el = el.parent
    return None


def main() -> None:
    html = PATH.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "lxml")
    print(f"== FILE == {PATH} ({len(html)} bytes)")

    price_nodes = soup.find_all(string=PRICE_RE)
    print(f"price text nodes: {len(price_nodes)}")

    tiles = []
    seen: set[int] = set()
    for pn in price_nodes:
        t = tile_of(pn)
        if t is not None and id(t) not in seen:
            seen.add(id(t))
            tiles.append(t)
    print(f"candidate product tiles: {len(tiles)}")

    cls = Counter()
    for t in tiles:
        for c in (t.get("class") or []):
            cls[c] += 1
    print("\n== COMMON TILE CLASSES (a stable one = good tile selector) ==")
    for c, n in cls.most_common(20):
        print(f"  {n:4d}  {c}")

    # Merchant/retailer hints: short text nodes near a price that aren't the title.
    print("\n== SAMPLE TILES (first 10): text split by field, plus link ==")
    for i, t in enumerate(tiles[:10], 1):
        classes = " ".join(t.get("class") or [])[:70]
        parts = [p for p in t.get_text("|", strip=True).split("|") if p.strip()]
        a = t.find("a", href=True)
        href = a["href"][:140] if a else "(no link)"
        # aria-labels often hold the clean product title
        labelled = t.find(attrs={"aria-label": True})
        aria = labelled["aria-label"][:120] if labelled else ""
        print(f"\n--- tile {i}  <{t.name} class='{classes}'>")
        print(f"    FIELDS: {parts[:12]}")
        if aria:
            print(f"    ARIA-LABEL: {aria}")
        print(f"    LINK: {href}")


if __name__ == "__main__":
    main()
