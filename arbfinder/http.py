"""Polite HTTP session: robots.txt compliance, per-host delays, retries."""

from __future__ import annotations

import logging
import random
import time
import urllib.robotparser
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 retail-arbitrage-finder/0.1"
)


class RobotsDisallowed(Exception):
    """Raised when robots.txt forbids fetching a URL."""


class PoliteSession:
    """requests.Session wrapper that honours robots.txt and rate-limits per host.

    - Checks robots.txt (cached per host) before every fetch and raises
      RobotsDisallowed rather than fetching a forbidden path.
    - Sleeps ``min_delay`` +/- jitter between requests to the same host.
    - Retries transient failures (connection errors, 429/5xx) with backoff.
    """

    def __init__(
        self,
        min_delay: float = 2.5,
        jitter: float = 0.75,
        max_retries: int = 3,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_UA,
    ):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-GB,en;q=0.9",
            }
        )
        self.min_delay = min_delay
        self.jitter = jitter
        self.max_retries = max_retries
        self.timeout = timeout
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # -- robots.txt ---------------------------------------------------------

    def _robots_for(self, url: str) -> urllib.robotparser.RobotFileParser | None:
        host = urlsplit(url).netloc
        if host in self._robots:
            return self._robots[host]
        robots_url = f"https://{host}/robots.txt"
        parser = urllib.robotparser.RobotFileParser()
        try:
            resp = self.session.get(robots_url, timeout=self.timeout)
            if resp.status_code >= 400:
                # No robots.txt served: nothing to obey.
                self._robots[host] = None
                return None
            parser.parse(resp.text.splitlines())
            self._robots[host] = parser
            return parser
        except requests.RequestException as exc:
            log.warning("Could not fetch %s (%s); proceeding cautiously", robots_url, exc)
            self._robots[host] = None
            return None

    def allowed(self, url: str) -> bool:
        parser = self._robots_for(url)
        if parser is None:
            return True
        return parser.can_fetch(self.session.headers["User-Agent"], url)

    # -- fetching -----------------------------------------------------------

    def _throttle(self, host: str) -> None:
        last = self._last_request.get(host)
        if last is not None:
            wait = self.min_delay + random.uniform(-self.jitter, self.jitter)
            elapsed = time.monotonic() - last
            if elapsed < wait:
                time.sleep(wait - elapsed)
        self._last_request[host] = time.monotonic()

    def get(self, url: str, *, respect_robots: bool = True, **kwargs) -> requests.Response:
        if respect_robots and not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")
        host = urlsplit(url).netloc
        kwargs.setdefault("timeout", self.timeout)
        backoff = 2.0
        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle(host)
            try:
                resp = self.session.get(url, **kwargs)
                if resp.status_code in (429, 500, 502, 503, 504) and attempt < self.max_retries:
                    log.warning("HTTP %s from %s, retrying in %.0fs", resp.status_code, url, backoff)
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                if attempt < self.max_retries:
                    log.warning("Request to %s failed (%s), retrying in %.0fs", url, exc, backoff)
                    time.sleep(backoff)
                    backoff *= 2
        raise requests.RequestException(f"Failed to fetch {url} after retries") from last_exc
