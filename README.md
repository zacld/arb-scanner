# retail-arbitrage-finder

Scrapes product prices from UK retail sites, cross-references the same
products on eBay UK via the official Browse API, and surfaces items where the
retail price is meaningfully below the going marketplace rate.

**Phase 1 (this repo today):** Argos → eBay UK *or* PriceRunner UK
(selectable with `--comparator`; PriceRunner needs no API key).
Phase 2 (planned): John Lewis, Sainsbury's as extra sources; Amazon UK as an
extra comparison target.

## Quick start

```bash
pip install -r requirements.txt

# See the full pipeline run offline on bundled fixture data (no keys needed).
# Default output is the merged report: one row per product with PriceRunner
# and eBay columns side by side; --comparator ebay|pricerunner runs just one.
python -m arbfinder demo
```

Demo output:

```
Product                                        Argos £    eBay £    Diff £   Diff %    N  Match
-----------------------------------------------------------------------------------------------
Tommee Tippee Perfect Prep Day & Night Mach…     89.99    114.99    +25.00   +27.8%    5  title
Ninja Foodi MAX Dual Zone Air Fryer AF400UK…    199.99    212.50    +12.50    +6.3%    4  ean
LEGO Technic 42151 Bugatti Bolide Race Car …     39.99     50.48    +10.49   +26.2%    5  ean
Sony WH-CH520 Wireless On-Ear Headphones - …     34.99     39.72     +4.73   +13.5%    4  title
Casio FX-83GTCW Scientific Calculator - Blue     13.99     13.50     -0.49    -3.5%    3  title
```

## Live runs

No key needed — the default comparator is PriceRunner UK:

```bash
python -m arbfinder scan "https://www.argos.co.uk/search/air-fryer/" \
    --max-products 20 --min-diff-pct 15
```

To compare against eBay UK instead (once your Browse API key is approved):
register a free app at <https://developer.ebay.com/my/keys> (Production
keyset), copy `.env.example` to `.env` and fill in the App ID / Cert ID, then
export them (`export $(grep -v '^#' .env | xargs)`) and add
`--comparator ebay` to the command above.

Useful flags:

| Flag | Meaning |
|---|---|
| `--comparator both\|pricerunner\|ebay` | Default `both`: runs every comparator and merges to one row per product (missing eBay creds just leave those columns empty). Name one to run it alone. |
| `--fetch-ean` | Visit each product page to extract the EAN for barcode-exact matching (slower — one extra request per product) |
| `--min-diff-pct N` | Only report rows where the marketplace is ≥ N% above the Argos price |
| `--sort net\|abs\|pct` | Sort by estimated net profit (default), £ gap, or % gap |
| `--fees PCT` | Marketplace selling fees % used for the net-profit column (default 13, roughly eBay/Amazon average) |
| `--postage GBP` | Flat postage cost deducted from net profit (default 0) |
| `--min-listings N` | Require ≥ N credible matches before trusting a price (default: comparator's own — 3 for eBay, 1 for PriceRunner) |
| `--min-score N` | Fuzzy title-match threshold, 0–100 (default 85) |
| `--delay N` | Minimum seconds between requests to the same host (default 2.5) |
| `--out FILE` | CSV output path (default `results.csv`) |

Results are printed as a table and written to CSV, sorted by estimated net
profit: `ebay_price − argos_price − (ebay_price × fees%) − postage`.
Net profit is only computed off the eBay resale price, since PriceRunner
prices are retailer asks, not sale proceeds — the merged report shows the
PriceRunner gap in its own column as a buy-side signal. Rows without an
eBay match keep their PriceRunner columns but leave net blank (`—`), and
sort after all netted rows, ordered by PriceRunner gap. Rows with negative
net profit still appear, sorted to the bottom.

## How it works

1. **Scrape Argos** (`arbfinder/sources/argos.py`). Search/category pages are
   server-rendered with product data embedded as JSON (`window.App` redux
   state). The parser tries three strategies in order so it degrades
   gracefully as Argos changes markup: embedded state JSON → JSON-LD →
   HTML product cards. If all three come up empty it logs that the page is
   likely JS-rendered/blocked (that's the cue to add a Playwright fetch).
2. **Find comparables** on the selected marketplace:
   - **PriceRunner UK** (`arbfinder/comparators/pricerunner.py`, default, no
     auth): queries the public JSON search endpoint that PriceRunner's own
     frontend uses. Chosen over Google Shopping because it returns clean
     structured data with a plain GET, while Google Shopping is heavily
     bot-defended with obfuscated markup. Results are aggregated catalog
     products priced at the lowest current retailer offer (so one match is
     meaningful — `min_listings` defaults to 1) and the search response
     carries no delivery cost (shipping reported as 0.00).
   - **eBay UK** (`arbfinder/comparators/ebay.py`, `--comparator ebay`):
     official Browse API with an application OAuth token (no user consent
     flow). EAN products are searched by `gtin` (barcode-exact); the rest by
     cleaned title, filtered to GB delivery, GBP, fixed-price, new condition.
3. **Match & filter** (`arbfinder/matching.py`). Titles are normalised
   (lowercase, strip pack sizes, punctuation, marketing filler) and compared
   with rapidfuzz `token_set_ratio`; title-search results below the threshold
   (default 85) are dropped — this is what keeps "Case for Sony WH-CH520"
   out of the Sony WH-CH520 price. Note the Browse API covers **active**
   listings; sold-price history needs eBay's restricted Marketplace Insights
   API, so "eBay price" here = median delivered price (item + postage) of
   credible active listings, which resists junk outliers on both ends.
4. **Report** (`arbfinder/report.py`). Console table + CSV, sorted by gap.

## Politeness

- `robots.txt` is fetched, cached and obeyed per host; disallowed URLs raise
  rather than fetch.
- Requests to the same host are spaced ≥ 2.5 s apart (with jitter) by default.
- Transient failures/429s back off exponentially; the tool never retries in a
  tight loop.
- Scrape → compare → output only. Nothing is listed, posted or automated
  against any marketplace.

## Tests

```bash
python -m pytest tests/
```

The demo fixtures mirror real page/API shapes and run through the exact same
parsing and pipeline code as live scans — only the network transport is
stubbed.

## Phase 2 notes (not built yet)

- **John Lewis**: add `arbfinder/sources/johnlewis.py` with the same
  `scrape(url) -> list[Product]` contract; the pipeline is source-agnostic.
- **Sainsbury's**: expect a JS-heavy Groceries SPA behind bot protection —
  budget for Playwright from the start rather than fighting
  requests+BeautifulSoup.
- **Amazon UK**: as a comparison target, prefer the official Product
  Advertising API (scraping Amazon is against their ToS and heavily
  defended). Same `search(query/gtin) -> listings` contract as the eBay
  client.
