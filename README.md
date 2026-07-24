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

## Dashboard (local web UI)

Prefer a browser to the command line? Run the local dashboard:

```bash
pip install -r requirements.txt
python -m arbfinder.dashboard
# open http://127.0.0.1:5000
```

**Type a search term and scan live** — set **Fetch via → "My Chrome"** and the
dashboard **launches a real Chrome for you** (a dedicated window, separate from
your normal browsing) the first time you scan, then reuses it. No terminal, no
file to save — it's fully point-and-click. (First run on a site, Akamai may show
a one-off challenge in that window; solve it once and scan again.) Pick the
**retailer** and **comparator** from the dropdowns, choose **eBay
Production/Sandbox**, and — for eBay — type your Client ID / Secret. Set **Min net
£** to hide rows that don't clear a real profit after fees + postage (blank =
show all, `0` = anything profitable). A saved-page **upload** is still there as an
always-works fallback. It **binds to 127.0.0.1 only** and credentials are sent
only to eBay's own API.

Tick **Remember these on this machine** to save the credentials (so you don't
retype them): they're written to `~/.arbfinder-credentials.json`, locked to
your user account where the OS supports it. This is the same plaintext-on-your-
own-machine pattern as `~/.netrc` — convenient, not encryption — so only use
it on a machine you trust. Environment variables (`EBAY_CLIENT_ID` /
`EBAY_CLIENT_SECRET`) override the file, and **Forget it** deletes it. The
saved secret is used for scans but is never rendered back into the page.

A Chromium window opens during scraping (Argos / Google); that's expected.

## Live runs (command line)

Search by **term** across one or more retail sites (no URL to paste). Default
source is Argos, default comparator is Google Shopping (no key):

```bash
python -m arbfinder scan "air fryer" --max-products 10
python -m arbfinder scan "air fryer" --source argos --source johnlewis
```

`--source` is repeatable. A full URL still works in place of a term. John
Lewis and Currys are **beta** — their parsers haven't been confirmed against
the live sites yet (save a page with `scripts/probe_page.py` if one returns
nothing, same as Argos was dialed in).

**Blocked by "Access Denied"?** Big UK retailers (Argos etc.) run Akamai bot
protection that defeats *every* automated fetch — local scripts, headless and
real-browser Playwright, and even third-party renderers like Jina Reader (their
servers get blocked too). The one thing Akamai lets through is a genuine human
browser on your own machine. So the reliable route is to save the page yourself
and parse it:

1. Open the search page in Chrome/Safari (it loads fine for you), e.g.
   `https://www.argos.co.uk/search/air-fryer/`
2. **Save As → "Webpage, HTML Only"** (Chrome) or **"Page Source"** (Safari),
   say `argos.html`
3. Parse it — no fetching, no bot wall:

```bash
python -m arbfinder scan --from-file argos.html --source argos --comparator google
```

### Demand-guided hunt (`hunt`) — it picks what to look for

Don't want to guess *what* to search? `hunt` runs a two-stage flow:

    demand-guided discovery  →  retail search  →  exact matching  →
    Amazon/eBay validation  →  fees + net profit + ROI  →  profit-first ranking

**Stage 1 — discovery** fuses trend/seasonal/marketplace **signals** into a
ranked list of *categories to investigate* (a `discovery_score`, scan-order
only). **Stage 2 — validation** searches those at retail, checks resale on
eBay/Amazon, and ranks the real products **profit-first**. A trend never floats a
low-profit item above a more profitable one.

```bash
python -m arbfinder hunt --via chrome --min-net 0 --min-roi 0.15
python -m arbfinder hunt --signal seasonal --signal manual \
    --trend-term "teeth whitening strips" --comparator ebay --min-net 0
```

Signals (`--signal`, repeatable; default `amazon seasonal manual`):

| Signal | What it is |
|---|---|
| `amazon` | Amazon Movers & Shakers / Best Sellers — real "already selling" demand (needs `--via chrome`) |
| `seasonal` | UK seasonal calendar (`arbfinder/trends/data/uk_seasons.json`) — ramps categories up *before* their peak (fans in summer, dehumidifiers in autumn, air fryers post-Christmas) so you source ahead |
| `manual` | categories you've spotted yourself, via `--trend-term "…"` (repeatable) — the reliable trend-spotter |
| `tiktok` | **experimental**, off by default: a public TikTok-Shop aggregator page via `--tiktok-url` / `ARBFINDER_TIKTOK_URL` |

**Profit-first ranking.** Each validated product gets an `opportunity_score`
(40% net + 25% ROI + 15% demand + 10% match + 5% trend + 5% seasonal), but the
results **sort by net profit → ROI → demand → match → trend/seasonal**. Hard
thresholds run *before* ranking: `--min-net`, `--min-roi` (e.g. `0.15` = 15%),
`--min-match` (0–1 match confidence). Sub-threshold items are excluded no matter
how trendy. Output is a table + `results.csv` of scored opportunities.

Cross-source agreement boosts discovery: a category on Amazon **and** your manual
list **and** in-season scans first. Signals are cached ~6h (`--fresh` to bypass).

In the **dashboard**, tick **“🔥 Or hunt trending”** for the browser version.

### Autonomous scraping via your own Chrome (`--via chrome`)

To scrape without saving pages by hand, drive the browser that already gets
through — **your real Chrome**. `--via chrome` now **starts that Chrome for you**
(dedicated debugging profile, warmed on the search page) and drives it over CDP:

```bash
python -m arbfinder scan "air fryer" --source argos --comparator ebay --via chrome
```

That's it — no separate launch step. It works because the tool uses your genuine
Chrome (real fingerprint + a persistent profile that keeps its Akamai clearance
cookie) rather than a detectable fresh browser, and it can hit many searches in
one go. If a first-ever run shows a challenge, solve it once in the window that
opens and re-run; the clearance then sticks.

Prefer to manage the window yourself? Launch it with `scripts/chrome-debug.command`
(or point `--cdp-url` at any Chrome you started with `--remote-debugging-port`)
and the tool will reuse it. Set `ARBFINDER_CHROME` if Chrome isn't in the default
install location.

Other fetch modes: `--via jina` routes through
[Jina Reader](https://jina.ai/reader) with a free `JINA_API_KEY`;
`--show-browser` uses a fresh local browser (usually blocked by Akamai);
`--from-file` parses a page you saved by hand (always works).

To compare against eBay UK instead (once your Browse API key is approved):
register a free app at <https://developer.ebay.com/my/keys> (Production
keyset), copy `.env.example` to `.env` and fill in the App ID / Cert ID, then
export them (`export $(grep -v '^#' .env | xargs)`) and add
`--comparator ebay` to the command above.

Useful flags:

| Flag | Meaning |
|---|---|
| `--comparator google\|ebay\|amazon\|pricerunner\|both` | Which price source. `amazon`: Amazon UK resale price via a real browser, no key (pair with `--via chrome`). `ebay`: Browse API (needs keys). `both`: merge PriceRunner + eBay to one row per product. |
| `--fetch-ean` | Visit each product page to extract the EAN for barcode-exact matching (slower — one extra request per product) |
| `--min-diff-pct N` | Only report rows where the marketplace is ≥ N% above the Argos price |
| `--min-net GBP` | Only show rows whose estimated net profit is ≥ this £ (after fees + postage). Hides rows with no net figure — so you see just the flips worth doing. `--min-net 0` = anything profitable |
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

1. **Scrape Argos** (`arbfinder/sources/argos.py`). The parser tries several
   strategies in order so it degrades gracefully as Argos changes markup:
   legacy `window.App` redux state → **Next.js flight data** (the current,
   2026 frontend: product objects arrive in `self.__next_f.push([1,"…"])`
   chunks, so the parser reassembles those chunks, pulls every
   `"productData":[…]` array, and hunts each product dict for its current
   price while skipping was/RRP/monthly-finance figures) → JSON-LD →
   HTML product cards (matched by `data-product-id`, monthly-payment
   subtrees stripped first).

   **Argos 403s plain HTTP clients** (bot protection), so on a 403 — or a
   200 whose HTML contains no product data — the scraper automatically
   retries through a real rendered browser (`arbfinder/browser.py`,
   Playwright + Chromium) with the same polite delays and robots.txt check.
   That fallback needs the optional dependency:

   ```bash
   pip install playwright
   playwright install chromium
   ```

   If headless mode is still blocked, `--show-browser` runs the fallback
   with a visible browser window, which passes bot checks more reliably.
2. **Find comparables** on the selected marketplace:
   - **PriceRunner UK** (`arbfinder/comparators/pricerunner.py`, default, no
     auth): queries the public JSON search endpoint that PriceRunner's own
     frontend uses. Chosen over Google Shopping because it returns clean
     structured data with a plain GET, while Google Shopping is heavily
     bot-defended with obfuscated markup. Results are aggregated catalog
     products priced at the lowest current retailer offer (so one match is
     meaningful — `min_listings` defaults to 1) and the search response
     carries no delivery cost (shipping reported as 0.00).
   - **Google Shopping** (`arbfinder/comparators/google_shopping.py`,
     `--comparator google`, **default**, no key): drives a real browser to
     "google the product" and reads the offer tiles — one per retailer
     (Argos, Currys, eBay, SharkNinja…), parsed by each tile's
     `aria-label="From <retailer>"` so it survives Google's obfuscated CSS.
     Retail asks, so a gap means cheaper/dearer elsewhere, not resale profit.
     Runs headed by default so Google's consent wall / any CAPTCHA is
     solvable; spaces requests out and stops cleanly if Google throttles.
   - **eBay UK** (`arbfinder/comparators/ebay.py`, `--comparator ebay`):
     official Browse API with an application OAuth token (no user consent
     flow). EAN products are searched by `gtin` (barcode-exact); the rest by
     cleaned title, filtered to GB delivery, GBP, fixed-price, new condition.
   - **Amazon UK** (`arbfinder/comparators/amazon.py`, `--comparator amazon`,
     no key): the "sell it on Amazon instead" comparator. Amazon has no free
     open pricing API, so this **prototype** reads the public search results
     through a real browser — best with `--via chrome`, which reuses your
     already-cleared Chrome over CDP (same trick that beats Argos's Akamai);
     otherwise it launches its own stealthed browser. Amazon is a *resale*
     venue, so its price is a sell price and **Net £ is computed** off it (like
     eBay). Current price only — **no sales rank (BSR) or exact FBA fees yet**;
     those are the paid next step (Keepa or Amazon's SP-API), worth adding once
     you've confirmed the gaps are there. Amazon may CAPTCHA automated access;
     reusing your real Chrome usually avoids it.
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
