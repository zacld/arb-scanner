# retail-arbitrage-finder

Scrapes product prices from UK retail sites, cross-references the same
products on eBay UK via the official Browse API, and surfaces items where the
retail price is meaningfully below the going marketplace rate.

**Phase 1 (this repo today):** Argos → eBay UK.
Phase 2 (planned): John Lewis, Sainsbury's as extra sources; Amazon UK as an
extra comparison target.

## Quick start

```bash
pip install -r requirements.txt

# See the full pipeline run offline on bundled fixture data (no keys needed):
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

1. Register a free app at <https://developer.ebay.com/my/keys> (Production
   keyset). Copy `.env.example` to `.env` and fill in the App ID / Cert ID,
   then export them (`export $(grep -v '^#' .env | xargs)`), or export
   `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET` directly.
2. Run a scan against any Argos search or category URL:

```bash
python -m arbfinder scan "https://www.argos.co.uk/search/air-fryer/" \
    --max-products 20 --min-diff-pct 15
```

Useful flags:

| Flag | Meaning |
|---|---|
| `--fetch-ean` | Visit each product page to extract the EAN for barcode-exact eBay matching (slower — one extra request per product) |
| `--min-diff-pct N` | Only report rows where eBay is ≥ N% above the Argos price |
| `--sort abs\|pct` | Sort by £ gap (default) or % gap |
| `--min-listings N` | Require ≥ N credible eBay matches before trusting a price (default 3) |
| `--min-score N` | Fuzzy title-match threshold, 0–100 (default 85) |
| `--delay N` | Minimum seconds between requests to the same host (default 2.5) |
| `--out FILE` | CSV output path (default `results.csv`) |

Results are printed as a table and written to CSV, sorted by biggest gap.

## How it works

1. **Scrape Argos** (`arbfinder/sources/argos.py`). Search/category pages are
   server-rendered with product data embedded as JSON (`window.App` redux
   state). The parser tries three strategies in order so it degrades
   gracefully as Argos changes markup: embedded state JSON → JSON-LD →
   HTML product cards. If all three come up empty it logs that the page is
   likely JS-rendered/blocked (that's the cue to add a Playwright fetch).
2. **Find comparables on eBay** (`arbfinder/comparators/ebay.py`). Official
   Browse API with an application OAuth token (no user consent flow). EAN
   products are searched by `gtin` (barcode-exact); the rest by cleaned title,
   filtered to GB delivery, GBP, fixed-price, new condition.
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
