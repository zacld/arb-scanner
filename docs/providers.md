# Price providers & the two-stage funnel

The tool prices products through **providers**, ordered by cost so it spends
browser/paid effort sparingly. This is the reusable spine: any venue that speaks
the comparator interface plugs into the same slots, so adding a paid API (e.g.
Keepa) changes nothing in the funnel, scoring, or UI.

## The interface

A provider is any object exposing:

```python
search(query=None, gtin=None) -> list[ComparableListing]   # priced listings
market_name: str            # "ebay" | "amazon" | ...
resale_market: bool         # True = its price is a resale value (nets profit)
default_min_listings: int   # min credible matches to trust a price
```

`arbfinder/providers.py` tags each with a **cost** and offers factories:

| Provider | Cost | Notes |
|---|---|---|
| `ebay_provider()` | `api-free` | eBay Browse API — free, fast, no browser |
| `amazon_provider()` | `browser` | Amazon via real Chrome — free but a page load each |
| `keepa_provider()` | `api-paid` | **drop-in slot** — Amazon price + sales-rank velocity |

## The funnel (`arbfinder/funnel.py`)

`find_opportunities(products, cheap_provider, rich_provider)`:

1. **Stage 1 — cheap, wide:** price the whole pool on `cheap_provider`
   (eBay's free API), apply the profit gates → a short survivor list.
2. **Stage 2 — expensive, narrow:** price ONLY the survivors on `rich_provider`
   (Amazon browser, or Keepa). The higher-net venue leads each row; the other is
   attached for comparison.

So a wide sweep costs ~O(pool) free API calls and only ~O(survivors) expensive
calls — a browser (or paid) hit never lands on a product that was never going to
clear the gate. `FunnelResult.staged` keeps the ungated cheap-stage results so
the UI can show near-misses for free.

## Adding Keepa (the paid drop-in)

Keepa (~£16/mo) gives Amazon price **plus real sales-rank/velocity** via an API —
replacing the flaky Amazon browser scrape *and* adding "does it actually sell"
data. To wire it in:

1. Implement `KeepaAmazonClient.search()` in `arbfinder/providers.py`: call
   Keepa's product endpoint (prefer the Argos EAN via `gtin`, else the title),
   return `ComparableListing` objects from the current Amazon price, and derive
   demand from the sales-rank drop count.
2. Use `keepa_provider(key)` in place of `amazon_provider()` as the funnel's rich
   stage.

Nothing else changes — the funnel, ranking, dashboard table and CSV are all
provider-agnostic. Because Keepa is an API, that stage then runs without a
browser (and without the Mac), partly cutting the Mac dependency loose.
