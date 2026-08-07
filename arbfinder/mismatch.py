"""Mismatch scan: cast a wide net, let the price gap surface the winners.

Instead of many narrow searches for specific product names, sweep a broad pool
of retail products (an Argos category or the Sale/Clearance section) and check
each one's resale value on TWO venues in priority order — Amazon first (the
lead number that drives the decision), eBay second (a backup/comparison). A row
survives because Argos is cheaper than what it resells for, not because we
guessed to look for it.

Buy side (v1): Argos, swept by category. Resale side: Amazon primary, eBay
secondary. Ranking/scoring is unchanged — this only changes WHERE the pool of
products comes from and that each is priced on two venues.
"""

from __future__ import annotations

import logging

from .sources.base import ScrapeBlocked, resolve_target

log = logging.getLogger(__name__)


# Broad Argos pools to sweep. These are best-effort entry URLs — Argos changes
# its browse paths, so the dashboard also accepts a pasted category URL or a
# plain search term, which sidesteps any stale link here. "sale" is the flagship
# pool: reduced stock is where retail dips below resale most often.
ARGOS_CATEGORIES: dict[str, tuple[str, str]] = {
    "sale": ("Sale / Clearance", "https://www.argos.co.uk/list/sale"),
    "technology": ("Technology", "https://www.argos.co.uk/browse/technology/c:30049/"),
    "home": ("Home & Garden", "https://www.argos.co.uk/browse/home-and-garden/c:29999/"),
    "toys": ("Toys", "https://www.argos.co.uk/browse/toys/c:30000/"),
}


def _page_url(base_url: str, page: int) -> str:
    """Argos paginates search/category pages with a trailing ``opt/page:N/``."""
    if page <= 1 or "opt/page:" in base_url:
        return base_url
    return f"{base_url.rstrip('/')}/opt/page:{page}/"


def sweep_products(scraper, target: str, *, source_name: str = "argos",
                   pages: int = 3, max_products: int | None = None):
    """Pull a wide pool of products from a category/search target, paginated.

    ``target`` may be a category key, a full Argos URL, or a plain search term
    (resolved to a search URL). Stops early on a blocked page or a page that
    adds nothing new (end of results). De-duplicates by product URL.
    """
    if target in ARGOS_CATEGORIES:
        base = ARGOS_CATEGORIES[target][1]
    else:
        base = resolve_target(source_name, target)

    seen: set[str] = set()
    out: list = []
    for page in range(1, pages + 1):
        url = _page_url(base, page)
        try:
            prods = scraper.scrape(url)
        except ScrapeBlocked as exc:
            log.info("sweep stopped at page %d: %s", page, exc)
            break
        new = [p for p in prods if p.url not in seen]
        if not new:
            break  # no fresh products — past the last page
        seen.update(p.url for p in new)
        out.extend(new)
        if max_products and len(out) >= max_products:
            return out[:max_products]
    return out
