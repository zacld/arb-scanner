"""Product title cleaning and fuzzy matching (rapidfuzz)."""

from __future__ import annotations

import re
import statistics

from rapidfuzz import fuzz

from .models import ComparableListing, Product

# Marketing/filler words that don't identify the product and hurt matching.
_NOISE_WORDS = {
    "new", "brand", "official", "genuine", "boxed", "sealed", "uk", "sale",
    "offer", "exclusive", "free", "delivery", "postage", "fast", "cheap",
    "bargain", "deal", "gift", "christmas", "bnib", "bnwt", "rrp", "latest",
}

# Pack sizes / multipliers: "2 pack", "x3", "3x", "pack of 4", "twin pack"
_PACK_RE = re.compile(
    r"\b(?:pack\s+of\s+\d+|\d+\s*(?:pack|pk)|x\s*\d+\b|\d+\s*x\b|twin\s+pack|multipack)\b",
    re.IGNORECASE,
)
_PUNCT_RE = re.compile(r"[^\w\s.]")
_SPACE_RE = re.compile(r"\s+")


def clean_title(title: str, brand: str | None = None) -> str:
    """Normalise a product title down to its identifying core.

    Lowercases, strips pack sizes, punctuation and marketing fluff. The brand
    is *kept* by default (it identifies the product for search) unless passed
    explicitly for removal — useful when comparing cross-site titles where one
    side omits the brand.
    """
    s = title.lower()
    if brand:
        s = s.replace(brand.lower(), " ")
    s = _PACK_RE.sub(" ", s)
    s = _PUNCT_RE.sub(" ", s)
    tokens = [t for t in _SPACE_RE.split(s) if t and t not in _NOISE_WORDS]
    return " ".join(tokens)


def title_similarity(a: str, b: str) -> float:
    """0-100 similarity between two raw titles, on their cleaned forms.

    token_set_ratio ignores word order and duplicate words, which suits
    marketplace titles stuffed with extra keywords.
    """
    return fuzz.token_set_ratio(clean_title(a), clean_title(b))


def filter_matches(
    product: Product,
    listings: list[ComparableListing],
    min_score: float = 85.0,
    matched_by: str = "title",
) -> list[ComparableListing]:
    """Keep listings that plausibly ARE the product.

    EAN-matched results are trusted with a lenient score gate (barcode search
    already pinned the product; the gate only drops grossly wrong hits like
    'case for X'). Title-search results must clear ``min_score``.
    """
    threshold = 50.0 if matched_by == "ean" else min_score
    out = []
    for l in listings:
        if not l.title:
            if matched_by == "ean":
                out.append(l)
            continue
        if title_similarity(product.name, l.title) >= threshold:
            out.append(l)
    return out


def representative_price(listings: list[ComparableListing]) -> tuple[float, str]:
    """Median delivered price, and the URL of the listing closest to it.

    Median resists both £1 junk/parts listings and £999 delusional sellers.
    """
    if not listings:
        raise ValueError("no listings")
    totals = [l.total for l in listings]
    med = statistics.median(totals)
    closest = min(listings, key=lambda l: abs(l.total - med))
    return med, closest.url
