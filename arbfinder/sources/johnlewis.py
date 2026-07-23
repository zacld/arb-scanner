"""John Lewis (johnlewis.com) search parser.

STATUS: beta — parser not yet confirmed against the live site. John Lewis is a
JS-heavy, Akamai-protected site (like Argos), so it runs through the browser
fallback and the shared schema.org/embedded-JSON parser. If a live scan
returns nothing, save the page with ``scripts/probe_page.py`` and adjust —
exactly how Argos was dialed in.
"""

from __future__ import annotations

from .generic import make_parser

BASE_URL = "https://www.johnlewis.com"

# Public entry point used by the source registry and tests.
parse_search_page = make_parser("johnlewis", BASE_URL)
