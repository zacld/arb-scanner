# Spec: sold-price & sell-through layer (design, not yet built)

## Why

Every price the tool shows today is a **median of ACTIVE listings** — asking
prices, not what buyers pay. That can't tell:

```
£22 → £50 with 40 completed sales/month   (real, buy it)
£22 → £50 because 3 sellers ask £50 and nobody buys   (mirage, skip)
```

This layer adds **real sold price + velocity** so `expected_sale_price`,
`demand_confidence` and the results label reflect what actually sells. It does
**not** change the profit-first ranking or the hard thresholds — it makes the
numbers they act on truthful.

Principle unchanged: trends/seasonal find the haystack; profit + ROI + **real
demand** decide the needle.

## Architecture — one pluggable interface

Mirror `TrendSignalProvider`. A sold-data source implements:

```python
@dataclass
class SoldStats:
    sold_median: float          # median completed-sale price (delivered)
    sold_count: int             # number of sold comps found in the window
    window_days: int            # lookback window (e.g. 30)
    sell_through: float | None  # sold / (sold + active), 0..1, when available
    source: str                 # "ebay_sold_scrape" | "ebay_insights" | "keepa"

class SoldPriceProvider(ABC):
    name: str
    def lookup(self, query: str | None = None, gtin: str | None = None,
               session=None, browser=None) -> SoldStats | None: ...
```

Returns `None` when it can't find credible sold comps — the caller then falls
back to the active-listing median (today's behaviour) with the honest caveat.

## Data-model changes

- `arbfinder/models.py::Comparison` — add optional `sold: SoldStats | None = None`.
- `arbfinder/trends/base.py::ArbitrageOpportunity` — add `sold_median`,
  `sold_count`, `sell_through`, and a `price_basis: str` ("sold" | "active").

## Scoring changes (`arbfinder/opportunity.py`)

1. **Expected sale price** — in `build_opportunity`, prefer the sold median:
   ```
   expected_sale_price = comp.sold.sold_median if comp.sold else comp.market_price
   price_basis = "sold" if comp.sold else "active"
   ```
   Net profit / ROI derive from whichever was used (unchanged formulas).
2. **demand_confidence becomes velocity-based** when sold data exists:
   ```
   demand_confidence = min(1.0, sold_count / VELOCITY_CAP)   # e.g. cap = 20 sales/30d
   ```
   Fall back to the current listing-count proxy only when `sold is None`.
3. `opportunity_score` weights unchanged (still 40% net + 25% ROI + 15% demand +
   10% match + 5% trend + 5% seasonal) — the **demand input** just becomes real.
4. **Sort unchanged**: net → ROI → demand → match → trend/seasonal.

## Label changes (both tables)

- `opportunity.py::format_opportunities` and `dashboard.py::_opportunities_table`:
  when `price_basis == "sold"`, header/caption reads **“Sold median · N sales/30d”**
  and drops the "asking prices" warning. When `"active"`, keep today's exact
  wording (median of ACTIVE listings, verify before buying).
- Add a **Sell-through** / **Sold/mo** column to the opportunity table.

## Data-source options (chosen later)

| Source | Data | Cost | Reliability | Notes |
|---|---|---|---|---|
| **Scrape eBay Sold** (rec. v1) | sold price + date → median + velocity | free | fragile (markup) | `…/sch/i.html?_nkw=<q>&LH_Sold=1&LH_Complete=1&_ipg=120`; drive the real browser we already use; **against eBay ToS**; dial in like Argos |
| **eBay Marketplace Insights API** | official sold/completed | free if granted | high | restricted — business-justification application, often declined for small sellers |
| **Keepa** | Amazon price history + BSR | ~£15/mo | high | Amazon resale venue only (not eBay); cleanest, no ToS grey |
| eBay Finding `findCompletedItems` | legacy sold | — | deprecated | being sunset; access-gated — **not recommended** |

### eBay Sold-scrape specifics (for the free v1)

- URL: `https://www.ebay.co.uk/sch/i.html?_nkw={query}&LH_Sold=1&LH_Complete=1&_ipg=120&_sop=13` (`_sop=13` = ended recently first).
- Parse each result: sold **price**, **postage**, and the **“Sold DD Mon YYYY”**
  date. `sold_median` = median(price+postage) after the same fuzzy title filter
  (`matching.filter_matches`) we already use for active listings. `sold_count` =
  comps within `window_days` (e.g. 30). `sell_through` = sold_count / (sold_count
  + active_count) using the Browse-API active count we already fetch.
- Reuse `BrowserFetcher` (CDP → your real Chrome), the same bot-clearance path.
- Cache per query short-TTL (reuse `trends/cache.py` pattern) — sold lookups are
  the slow part.

## Integration points (files)

- New `arbfinder/soldprice/` (base + provider(s)), mirroring `arbfinder/trends/`.
- `arbfinder/pipeline.py::compare_products` — after building each `Comparison`,
  optionally attach `SoldStats` via the configured provider (guarded by a flag /
  `--sold`), so `scan` and `hunt` both benefit.
- `arbfinder/opportunity.py` — price-basis selection + velocity demand (above).
- `arbfinder/cli.py` (`hunt`, `scan`) — `--sold {off,ebay-scrape,insights,keepa}`
  flag; dashboard — a "Use sold prices" toggle.
- Display: label/column changes in both tables + CSV columns
  (`sold_median`, `sold_count`, `sell_through`, `price_basis`).

## Fallback & honesty

- No sold data for a product → keep the active median and the current
  "asking, not sold" caveat. Never silently present active as sold.
- Mixed tables show `price_basis` per row so it's always clear which is which.

## Testing

- Fixture: a saved eBay Sold results page → parser yields expected
  `sold_median`, `sold_count`, dates within/outside the window.
- `build_opportunity` uses sold median when present (velocity demand), active
  median otherwise (listing-count demand) — both paths asserted.
- Label/column: "Sold median" wording when basis is sold; "ACTIVE listings"
  caveat when active.

## Risks

- eBay Sold scraping is ToS-grey and markup-fragile (same tradeoff accepted for
  Amazon). The provider interface makes it swappable for the official API later
  without touching scoring or display.
- Adds latency (one extra browser fetch per product) — mitigated by caching and
  by only running on rows that already pass the price/ROI gates if we choose to
  score first, then enrich the survivors.

## Suggested rollout

1. Interface + `SoldStats` + scoring/label changes behind `--sold off` default
   (no behaviour change until enabled).
2. eBay Sold-scrape provider (free) + fixture tests.
3. Wire `--sold` / dashboard toggle; enrich only gate-passing rows to bound cost.
4. Later: official eBay Insights and/or Keepa providers as drop-ins.
