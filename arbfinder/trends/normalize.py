"""Turn raw trend signals into canonical UK retail categories (rules v1).

This is the seam an LLM slots into later: swap ``make_rules_classifier`` for an
``llm_classifier`` with the same ``(TrendSignal) -> str | None`` shape to handle
novel trends and ambiguous names. Everything else (providers, engine, ranking)
stays put.
"""

from __future__ import annotations

import re

from .base import TrendSignal

# (canonical category, trigger substrings). First match wins. Deliberately
# retail-buyable categories only — chatter that maps to nothing is dropped.
CATEGORY_RULES: list[tuple[str, list[str]]] = [
    ("teeth whitening strips", ["whitening strip", "teeth whiten", "whiten teeth"]),
    ("air fryer", ["air fryer", "airfryer", "air-fryer"]),
    ("tower fan", ["tower fan", "air circulator"]),
    ("pedestal fan", ["pedestal fan"]),
    ("desk fan", ["desk fan", "usb fan"]),
    ("portable air conditioner", ["portable air con", "air conditioner", "aircon", "portable ac"]),
    ("dehumidifier", ["dehumidifier", "damp", "condensation"]),
    ("electric heater", ["electric heater", "fan heater", "plug in heater", "oil heater"]),
    ("electric blanket", ["electric blanket", "heated blanket", "heated throw"]),
    ("paddling pool", ["paddling pool", "kids pool", "swimming pool"]),
    ("bbq", ["bbq", "barbecue", "barbeque", "pizza oven", "griddle"]),
    ("garden furniture", ["garden furniture", "rattan", "patio set", "outdoor furniture"]),
    ("pressure washer", ["pressure washer", "jet wash"]),
    ("lawnmower", ["lawnmower", "lawn mower", "grass trimmer", "strimmer"]),
    ("robot vacuum", ["robot vacuum", "robot hoover", "robovac"]),
    ("stanley cup", ["stanley cup", "stanley tumbler", "quencher"]),
    ("england football shirt", ["england shirt", "world cup", "euros", "football shirt", "three lions"]),
    ("football", ["football", "soccer ball"]),
    ("television", ["4k tv", "smart tv", "oled tv", "television", " tv "]),
    ("headphones", ["headphone", "earbuds", "earphones"]),
    ("laptop", ["laptop", "chromebook", "notebook computer"]),
    ("printer", ["printer", "ink cartridge"]),
    ("calculator", ["calculator"]),
    ("school bag", ["school bag", "backpack", "rucksack"]),
    ("lego", ["lego"]),
    ("gaming console", ["playstation", "ps5", "xbox", "nintendo switch", "console"]),
    ("bathroom scales", ["bathroom scale", "body scale", "smart scale"]),
    ("fitness equipment", ["dumbbell", "kettlebell", "treadmill", "exercise bike", "resistance band"]),
    ("storage boxes", ["storage box", "storage container", "storage basket"]),
    ("christmas decorations", ["christmas tree", "christmas lights", "christmas decoration", "baubles"]),
]

_MODEL_RE = re.compile(r"\b([A-Z]{1,4}[- ]?\d{2,5}[A-Z]{0,3})\b")
_PUNCT = re.compile(r"[^\w\s]")
_SPACE = re.compile(r"\s+")


def _clean_generic(title: str) -> str:
    """Fallback category for a real product with no lexicon hit: a short,
    model-stripped version of its title (keeps it a usable search term)."""
    s = _MODEL_RE.sub(" ", title)
    s = _PUNCT.sub(" ", s)
    return _SPACE.sub(" ", s).strip().lower()[:50]


def lexicon_category(text: str) -> str | None:
    low = f" {text.lower()} "
    for category, triggers in CATEGORY_RULES:
        if any(t in low for t in triggers):
            return category
    return None


def extract_lead(title: str) -> str | None:
    """A specific-product lead (brand + model) from a marketplace title."""
    m = _MODEL_RE.search(title)
    return title[:60].strip() if m else None


def make_rules_classifier(keep_unmapped_sources: frozenset[str] = frozenset()):
    """Return ``classify(signal) -> category | None``.

    A preset ``product_category`` (e.g. from the seasonal provider) is honoured.
    Otherwise the lexicon decides; unmapped signals are dropped unless their
    source is trusted to be a real product (Amazon/manual), where a cleaned
    title is used so genuine products aren't lost.
    """
    def classify(sig: TrendSignal) -> str | None:
        if sig.product_category:
            return sig.product_category
        text = " ".join([sig.original_title, *sig.keywords])
        cat = lexicon_category(text)
        if cat:
            return cat
        if sig.source in keep_unmapped_sources:
            return _clean_generic(sig.original_title) or None
        return None

    return classify
