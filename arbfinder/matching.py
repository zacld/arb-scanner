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

# Quantities that mean "more than one unit in the box" — a multipack listing's
# price is a multiple of a single unit's and must be discarded, not laundered
# into the median. Captures the quantity (>=2) where there is one; a bare
# "twin pack" / "multipack" implies >=2 with no number to capture.
_MULTIPACK_RE = re.compile(
    r"\b(?:pack\s+of\s+(\d+)|(\d+)\s*(?:pack|pk)\b|(?:set|bundle|lot|case|box)\s+of\s+(\d+)"
    r"|x\s*(\d+)\b|(\d+)\s*x\b|twin\s+pack|multi[\s-]?pack)\b",
    re.IGNORECASE,
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_LEADING_ALPHA_RE = re.compile(r"^[a-z]+")


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


def is_multipack(title: str) -> bool:
    """True when the title advertises more than one unit (2-pack, twin pack,
    bundle of 3, x4 …). A bare "pack" with no quantity (e.g. "battery pack")
    is not treated as a multipack.
    """
    for m in _MULTIPACK_RE.finditer(title):
        qty = next((g for g in m.groups() if g), None)
        if qty is None:  # "twin pack" / "multipack" — implies >=2
            return True
        if int(qty) >= 2:
            return True
    return False


def _norm_model(text: str) -> str:
    """Alphanumeric-only, lowercased form used to compare model numbers across
    punctuation differences (``WH-CH520`` == ``WHCH520`` == ``wh ch520``)."""
    return _NON_ALNUM_RE.sub("", text.lower())


def _model_tokens(title: str) -> list[str]:
    """Model-number-like tokens in a title: whitespace-split words that, once
    stripped to alphanumerics, mix letters and digits and are >=4 chars
    (``AF100UK``, ``wh-ch520`` -> ``whch520``). Filler like ``black`` or
    ``2024`` is excluded."""
    toks = []
    for raw in title.split():
        t = _norm_model(raw)
        if len(t) >= 4 and any(c.isalpha() for c in t) and any(c.isdigit() for c in t):
            toks.append(t)
    return toks


def _model_verdict(product_model: str, title: str) -> str | None:
    """Compare a product's model number against a listing title.

    Returns ``"match"`` when the exact model appears in the title (trusted
    regardless of title fuzz), ``"variant"`` when the title carries a
    *different* model from the same family (e.g. AF300UK vs AF100UK — a hard
    reject), or ``None`` when the title says nothing about the model (defer to
    fuzzy title matching).
    """
    model = _norm_model(product_model)
    if not model:
        return None
    if model in _norm_model(title):
        return "match"
    prefix = _LEADING_ALPHA_RE.match(model)
    prefix = prefix.group(0) if prefix else ""
    if len(prefix) >= 2:
        for tok in _model_tokens(title):
            if tok != model and tok.startswith(prefix):
                return "variant"
    return None


def filter_matches(
    product: Product,
    listings: list[ComparableListing],
    min_score: float = 85.0,
    matched_by: str = "title",
) -> list[ComparableListing]:
    """Keep listings that plausibly ARE the product.

    Matching, in order of trust:

    * **Multipacks are dropped** whenever the product itself is a single unit —
      a 2-pack's price is ~2x and would inflate the median.
    * **Model number wins** when the product has one: an exact model in the
      title is trusted no matter the fuzzy score; a different model from the
      same family (AF300UK when we want AF100UK) is rejected outright.
    * Otherwise fall back to the fuzzy title gate. EAN-matched results use a
      lenient gate (barcode search already pinned the product; the gate only
      drops grossly wrong hits like 'case for X'); title-search results must
      clear ``min_score``.
    """
    threshold = 50.0 if matched_by == "ean" else min_score
    product_is_pack = is_multipack(product.name)
    out = []
    for l in listings:
        if not l.title:
            if matched_by == "ean":
                out.append(l)
            continue
        if is_multipack(l.title) and not product_is_pack:
            continue
        if product.model_number:
            verdict = _model_verdict(product.model_number, l.title)
            if verdict == "match":
                out.append(l)
                continue
            if verdict == "variant":
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
