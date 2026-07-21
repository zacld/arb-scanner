"""eBay Browse API client (application-token OAuth, no user consent needed).

Register an app at https://developer.ebay.com/my/keys to get a client id and
secret, then export EBAY_CLIENT_ID and EBAY_CLIENT_SECRET (see .env.example).

The Browse API returns *active* listings. Sold-price history needs the
restricted Marketplace Insights API, so we approximate "what it sells for"
with the median delivered price of well-matched active fixed-price listings.
"""

from __future__ import annotations

import base64
import logging
import time

import requests

from ..models import ComparableListing

log = logging.getLogger(__name__)

_HOSTS = {
    "PRODUCTION": "https://api.ebay.com",
    "SANDBOX": "https://api.sandbox.ebay.com",
}
_SCOPE = "https://api.ebay.com/oauth/api_scope"


class EbayAuthError(Exception):
    pass


class EbayBrowseClient:
    def __init__(
        self,
        client_id: str,
        client_secret: str,
        env: str = "PRODUCTION",
        marketplace: str = "EBAY_GB",
        session: requests.Session | None = None,
        min_delay: float = 0.5,
    ):
        if env not in _HOSTS:
            raise ValueError(f"env must be one of {sorted(_HOSTS)}")
        self.host = _HOSTS[env]
        self.client_id = client_id
        self.client_secret = client_secret
        self.marketplace = marketplace
        self.session = session or requests.Session()
        self.min_delay = min_delay
        self._token: str | None = None
        self._token_expiry = 0.0
        self._last_call = 0.0

    # -- auth ---------------------------------------------------------------

    def _get_token(self) -> str:
        if self._token and time.time() < self._token_expiry - 60:
            return self._token
        creds = base64.b64encode(f"{self.client_id}:{self.client_secret}".encode()).decode()
        resp = self.session.post(
            f"{self.host}/identity/v1/oauth2/token",
            headers={
                "Authorization": f"Basic {creds}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials", "scope": _SCOPE},
            timeout=30,
        )
        if resp.status_code != 200:
            raise EbayAuthError(f"eBay token request failed ({resp.status_code}): {resp.text[:300]}")
        payload = resp.json()
        self._token = payload["access_token"]
        self._token_expiry = time.time() + int(payload.get("expires_in", 7200))
        return self._token

    # -- search -------------------------------------------------------------

    def _throttle(self) -> None:
        elapsed = time.time() - self._last_call
        if elapsed < self.min_delay:
            time.sleep(self.min_delay - elapsed)
        self._last_call = time.time()

    def search(
        self,
        query: str | None = None,
        gtin: str | None = None,
        limit: int = 25,
        condition: str | None = "NEW",
        fixed_price_only: bool = True,
    ) -> list[ComparableListing]:
        """Search item summaries by free-text query or GTIN/EAN barcode."""
        if not query and not gtin:
            raise ValueError("Provide query or gtin")
        params: dict[str, str] = {"limit": str(limit)}
        if gtin:
            params["gtin"] = gtin
        else:
            params["q"] = query
        filters = ["deliveryCountry:GB", "priceCurrency:GBP"]
        if fixed_price_only:
            filters.append("buyingOptions:{FIXED_PRICE}")
        if condition:
            filters.append("conditions:{%s}" % condition)
        params["filter"] = ",".join(filters)

        self._throttle()
        resp = self.session.get(
            f"{self.host}/buy/browse/v1/item_summary/search",
            params=params,
            headers={
                "Authorization": f"Bearer {self._get_token()}",
                "X-EBAY-C-MARKETPLACE-ID": self.marketplace,
                "X-EBAY-C-ENDUSERCTX": "contextualLocation=country=GB",
            },
            timeout=30,
        )
        if resp.status_code != 200:
            log.warning("eBay search failed (%s): %s", resp.status_code, resp.text[:300])
            return []
        return parse_item_summaries(resp.json())


def parse_item_summaries(payload: dict) -> list[ComparableListing]:
    listings: list[ComparableListing] = []
    for item in payload.get("itemSummaries", []) or []:
        price_info = item.get("price") or {}
        try:
            price = float(price_info["value"])
        except (KeyError, TypeError, ValueError):
            continue
        shipping = 0.0
        for opt in item.get("shippingOptions") or []:
            cost = (opt or {}).get("shippingCost") or {}
            try:
                shipping = float(cost["value"])
                break
            except (KeyError, TypeError, ValueError):
                continue
        listings.append(
            ComparableListing(
                title=item.get("title", ""),
                price=price,
                shipping=shipping,
                url=item.get("itemWebUrl", ""),
                condition=item.get("condition", ""),
            )
        )
    return listings
