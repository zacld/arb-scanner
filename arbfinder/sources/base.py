"""Source registry: pluggable retail sites searched by keyword, not URL.

Each source knows how to turn a search *term* into its own search URL and how
to parse the resulting page into Products. A shared browser-backed scraper
does the fetching (plain HTTP, then a rendered-browser fallback with a retry
on transient bot-protection blocks), so adding a site is just a URL builder +
a parser.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, quote_plus

from ..browser import BrowserFetcher
from ..http import PoliteSession, RobotsDisallowed
from ..models import Product
from .argos import looks_like_denial_page
from .argos import parse_search_page as argos_parse
from .generic import make_parser

log = logging.getLogger(__name__)


class ScrapeBlocked(Exception):
    """A source refused both the plain-HTTP fetch and the browser fallback."""


def _is_url(text: str) -> bool:
    return text.startswith(("http://", "https://"))


@dataclass
class Source:
    name: str
    label: str
    build_search_url: Callable[[str], str]
    parse: Callable[[str], list[Product]]
    verified: bool = True  # False = parser not yet confirmed against the live site


def _argos_search_url(term: str) -> str:
    # Argos search paths are hyphen-slugged and lowercase: "air fryer" ->
    # /search/air-fryer/ (NOT /search/air%20fryer/, which Akamai treats oddly).
    slug = re.sub(r"\s+", "-", term.strip().lower())
    return f"https://www.argos.co.uk/search/{quote(slug)}/"


def _query_url(template: str):
    """Search-URL builder for sites that take the term as a ?q= style param."""
    return lambda term: template.format(q=quote_plus(term.strip()))


# Adding a retailer that emits schema.org / embedded-JSON products is now just
# one line here: a label, a search-URL builder, and the shared generic parser.
# `verified=False` marks a parser not yet confirmed against the live site —
# save a page with scripts/probe_page.py to confirm/adjust, then flip it True.
SOURCES: dict[str, Source] = {
    "argos": Source("argos", "Argos", _argos_search_url, argos_parse, verified=True),
    "johnlewis": Source(
        "johnlewis", "John Lewis",
        _query_url("https://www.johnlewis.com/search?search-term={q}"),
        make_parser("johnlewis", "https://www.johnlewis.com"), verified=False),
    "currys": Source(
        "currys", "Currys",
        _query_url("https://www.currys.co.uk/search?q={q}"),
        make_parser("currys", "https://www.currys.co.uk"), verified=False),
}


def resolve_target(source_name: str, term_or_url: str) -> str:
    """A full URL is used as-is; anything else is treated as a search term."""
    if _is_url(term_or_url):
        return term_or_url
    return SOURCES[source_name].build_search_url(term_or_url)


class BrowserBackedScraper:
    """Fetches a source's search page (plain HTTP → browser fallback) and parses it."""

    def __init__(self, source: Source, session: PoliteSession | None = None,
                 browser: BrowserFetcher | None = None):
        self.source = source
        self.session = session or PoliteSession()
        self.browser = browser
        self._used_browser = False

    def _browser_html(self, url: str, denial_retries: int = 1) -> str | None:
        if not self.session.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")
        if self.browser is None:
            self.browser = BrowserFetcher(min_delay=self.session.min_delay,
                                          jitter=self.session.jitter)
        html = self.browser.fetch(url)
        attempts = 0
        while html and looks_like_denial_page(html) and attempts < denial_retries:
            attempts += 1
            wait = 3.0 * attempts
            log.warning("%s served a bot-protection page; retrying in %.0fs (%d/%d)",
                        self.source.label, wait, attempts, denial_retries)
            time.sleep(wait)
            html = self.browser.fetch(url)
        return html

    def _fetch_html(self, url: str) -> str | None:
        if self._used_browser:
            return self._browser_html(url)
        resp = self.session.get(url)
        if resp.status_code == 200:
            return resp.text
        log.warning("HTTP %s from %s — falling back to browser rendering",
                    resp.status_code, url)
        html = self._browser_html(url)
        if html is not None:
            self._used_browser = True
        return html

    def scrape(self, term_or_url: str, max_products: int | None = None) -> list[Product]:
        url = resolve_target(self.source.name, term_or_url)
        html = self._fetch_html(url)
        products = self.source.parse(html) if html else []
        if not products and html and not self._used_browser:
            html = self._browser_html(url)
            if html:
                self._used_browser = True
                products = self.source.parse(html)
        if not products:
            denied = html and looks_like_denial_page(html)
            reason = (
                f"{self.source.label} bot protection served an Access Denied page "
                "(often intermittent/IP-based — wait a few minutes and retry)."
                if denied else
                f"no recognisable product data was found on the {self.source.label} page"
                + ("" if self.source.verified else
                   f" — the {self.source.label} parser is not yet confirmed against the "
                   "live site; save the page with scripts/probe_page.py to adjust it.")
            )
            raise ScrapeBlocked(f"Could not scrape {self.source.label} ({url}).\n{reason}")
        for p in products:
            p.source = self.source.name
        if max_products:
            products = products[:max_products]
        return products


def make_scraper(source_name: str, session: PoliteSession | None = None,
                 browser: BrowserFetcher | None = None) -> BrowserBackedScraper:
    return BrowserBackedScraper(SOURCES[source_name], session, browser)
