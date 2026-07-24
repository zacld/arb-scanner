"""Command-line interface.

Live scan:
    python -m arbfinder scan "https://www.argos.co.uk/search/air-fryer/" --max-products 20

Offline demo (bundled fixtures, no network, no API key):
    python -m arbfinder demo
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .pipeline import compare_products, compare_products_multi
from .report import (
    format_merged_table,
    format_table,
    sort_comparisons,
    sort_merged,
    write_csv,
    write_merged_csv,
)


def _output(comparisons, args, out_path=None) -> None:
    comparisons = sort_comparisons(
        comparisons, by=args.sort, fees_pct=args.fees, postage=args.postage
    )
    if args.min_diff_pct is not None:
        comparisons = [c for c in comparisons if c.diff_pct >= args.min_diff_pct]
    if args.min_net is not None:
        comparisons = [
            c for c in comparisons
            if (n := c.net_profit(args.fees, args.postage)) is not None and n >= args.min_net
        ]
    if not comparisons:
        print("No comparisons produced (no products scraped, too few credible matches, "
              "or none cleared --min-net).")
        return
    print(format_table(comparisons, fees_pct=args.fees, postage=args.postage))
    path = write_csv(comparisons, out_path or args.out, fees_pct=args.fees, postage=args.postage)
    print(f"\n{len(comparisons)} result(s) written to {path}")


def _output_merged(rows, args) -> None:
    if args.sort != "net":
        print("(note: the merged report always sorts by net profit, "
              "with PriceRunner gap as the tiebreaker)")
    rows = sort_merged(rows, fees_pct=args.fees, postage=args.postage)
    if args.min_diff_pct is not None:
        rows = [r for r in rows
                if r.best_diff_pct is not None and r.best_diff_pct >= args.min_diff_pct]
    if args.min_net is not None:
        rows = [
            r for r in rows
            if (n := r.net_profit(args.fees, args.postage)) is not None and n >= args.min_net
        ]
    if not rows:
        print("No comparisons produced (no products scraped, too few credible matches, "
              "or none cleared --min-net).")
        return
    print(format_merged_table(rows, fees_pct=args.fees, postage=args.postage))
    path = write_merged_csv(rows, args.out, fees_pct=args.fees, postage=args.postage)
    print(f"\n{len(rows)} result(s) written to {path}")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default="results.csv", help="CSV output path (default: results.csv)")
    p.add_argument("--sort", choices=["net", "abs", "pct"], default="net",
                   help="Sort by estimated net profit (default), £ gap (abs) or %% gap (pct); "
                        "rows without a net figure sort after those with one")
    p.add_argument("--fees", type=float, default=13.0, metavar="PCT",
                   help="Marketplace selling fees as %% of sale price for the net-profit "
                        "column (default: 13, roughly eBay/Amazon average)")
    p.add_argument("--postage", type=float, default=0.0, metavar="GBP",
                   help="Flat postage cost in £ deducted from net profit (default: 0)")
    p.add_argument("--min-diff-pct", type=float, default=None,
                   help="Only show rows where eBay is at least this %% above the retail price")
    p.add_argument("--min-net", type=float, default=None, metavar="GBP",
                   help="Only show rows whose estimated net profit is at least this many £ "
                        "(after fees + postage). Drops rows with no net figure — so you see "
                        "just the flips worth doing. Try --min-net 0 for 'anything profitable'.")
    p.add_argument("--min-listings", type=int, default=None,
                   help="Minimum credible matches required (default: comparator's own — "
                        "3 for eBay listings, 1 for PriceRunner's aggregated products)")
    p.add_argument("--min-score", type=float, default=85.0,
                   help="Fuzzy title-match threshold 0-100 (default: 85)")
    p.add_argument("-v", "--verbose", action="store_true")


def _resolve_ebay_creds(wanted, comparator):
    """Read eBay creds from the env. May drop eBay from ``wanted`` (for 'both')
    or ask the caller to exit. Returns (client_id, client_secret, wanted, exit)."""
    cid = os.environ.get("EBAY_CLIENT_ID")
    sec = os.environ.get("EBAY_CLIENT_SECRET")
    if "ebay" in wanted and (not cid or not sec):
        if comparator == "both":
            print("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set — continuing with "
                  "PriceRunner only (eBay columns will be empty).", file=sys.stderr)
            wanted = [w for w in wanted if w != "ebay"]
        else:
            print("EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set.\n"
                  "Register a (free) app at https://developer.ebay.com/my/keys and export both,\n"
                  "use --comparator pricerunner (no key needed), or run "
                  "`python -m arbfinder demo` to see the pipeline on fixture data.",
                  file=sys.stderr)
            return cid, sec, wanted, 2
    return cid, sec, wanted, None


def _make_clients(wanted, args, session, cdp, client_id, client_secret):
    """Build the comparator clients for ``wanted``; returns (clients, closeables)."""
    clients, closeables = [], []
    for name in wanted:
        if name == "ebay":
            from .comparators.ebay import EbayBrowseClient
            clients.append(EbayBrowseClient(client_id, client_secret, env=args.ebay_env))
        elif name == "google":
            from .comparators.google_shopping import GoogleShoppingClient
            gc = GoogleShoppingClient(headless=args.headless_compare,
                                      min_delay=max(args.delay, 3.0))
            clients.append(gc)
            closeables.append(gc)
        elif name == "amazon":
            from .comparators.amazon import AmazonClient
            ac = AmazonClient(cdp_url=cdp, headless=args.headless_compare,
                              min_delay=max(args.delay, 3.0))
            clients.append(ac)
            closeables.append(ac)
        else:
            from .comparators.pricerunner import PriceRunnerClient
            clients.append(PriceRunnerClient(session))
    return clients, closeables


def _hunt(args) -> int:
    """Discover trending items from Amazon, hunt them at retail, report the profitable ones."""
    from .browser import BrowserFetcher
    from .discover import discover_bestsellers
    from .http import PoliteSession
    from .sources.base import ScrapeBlocked, SOURCES, make_scraper

    comparator = "ebay" if args.comparator == "both" else args.comparator
    wanted = [comparator]
    client_id, client_secret, wanted, exit_code = _resolve_ebay_creds(wanted, comparator)
    if exit_code is not None:
        return exit_code

    session = PoliteSession(min_delay=args.delay)
    source = (args.source or ["argos"])[0]
    fetch_mode = args.via
    cdp = None
    if args.via == "chrome":
        from .chrome_launch import ensure_chrome
        ok, msg = ensure_chrome(
            args.cdp_url, open_url="https://www.amazon.co.uk/gp/movers-and-shakers")
        print(f"  {msg}")
        if not ok:
            return 1
        browser = BrowserFetcher(cdp_url=args.cdp_url, min_delay=args.delay)
        fetch_mode = "browser"
        cdp = args.cdp_url
    elif args.show_browser:
        browser = BrowserFetcher(headless=False, min_delay=args.delay)
    else:
        browser = BrowserFetcher(headless=True, min_delay=args.delay)

    print("Discovering what's selling right now on Amazon …")
    ideas = discover_bestsellers(browser, limit=args.limit)
    if not ideas:
        print("Couldn't read Amazon's best-seller pages (bot check or markup change).\n"
              "Use --via chrome and browse amazon.co.uk once in that window first.",
              file=sys.stderr)
        return 1
    print(f"  {len(ideas)} trending item(s) to hunt:")
    for idea in ideas:
        px = f" ~£{idea.amazon_price:.2f}" if idea.amazon_price else ""
        print(f"    • {idea.term}{px}  [{idea.reason}]")

    products = []
    scraper = make_scraper(source, session, browser, fetch_mode=fetch_mode)
    for idea in ideas:
        try:
            found = scraper.scrape(idea.term, max_products=args.per_item)
            products.extend(found)
            print(f"  {SOURCES[source].label}: '{idea.term}' -> {len(found)} product(s)")
        except ScrapeBlocked as exc:
            print(f"  {idea.term}: {str(exc).splitlines()[-1]}", file=sys.stderr)
    try:  # done scraping sources; free the source browser (CDP: just our tab)
        browser.close()
    except Exception:  # noqa: BLE001
        pass
    if not products:
        print(f"None of the trending items were found at {SOURCES[source].label}.",
              file=sys.stderr)
        return 1

    clients, closeables = _make_clients(wanted, args, session, cdp, client_id, client_secret)
    print(f"\nChecking {len(products)} product(s) for resale margin on {comparator} …")
    try:
        comparisons = compare_products(products, clients[0], min_score=args.min_score,
                                       min_listings=args.min_listings)
        _output(comparisons, args)
    finally:
        for c in closeables:
            c.close()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arbfinder", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Search retail sites by term and compare prices elsewhere")
    scan.add_argument("query", nargs="?", help="Search term (e.g. \"air fryer\") — or a "
                      "full source URL. Optional when --from-file is given.")
    from .sources.base import SOURCES as _SRC
    scan.add_argument("--source", action="append", choices=list(_SRC),
                      help="Retail site(s) to search; repeatable (default: argos). "
                           "Non-Argos sources are beta — parsers not yet confirmed "
                           "against their live sites.")
    scan.add_argument("--from-file", metavar="PATH",
                      help="Parse a page HTML file you saved from your OWN browser instead "
                           "of fetching it. The reliable way past Akamai bot protection "
                           "(Argos etc.): open the search page in Chrome/Safari, Save As "
                           "'Webpage, HTML Only', then point --source's parser at it.")
    scan.add_argument("--comparator",
                      choices=["google", "both", "pricerunner", "ebay", "amazon"],
                      default="google",
                      help="Price source. 'google' (default): Google Shopping via a real "
                           "browser, no API key — what it costs across retailers. 'ebay': "
                           "Browse API (needs EBAY_CLIENT_ID/EBAY_CLIENT_SECRET). "
                           "'amazon': Amazon UK resale price via a real browser, no key "
                           "(best with --via chrome — reuses your Chrome; current price "
                           "only, no sales rank yet). 'pricerunner': its private API "
                           "(currently unavailable). 'both': merge PriceRunner + eBay.")
    scan.add_argument("--max-products", type=int, default=25)
    scan.add_argument("--fetch-ean", action="store_true",
                      help="Visit each product page to extract the EAN (slower, better matching)")
    scan.add_argument("--ebay-env", choices=["PRODUCTION", "SANDBOX"], default="PRODUCTION")
    scan.add_argument("--delay", type=float, default=2.5,
                      help="Minimum seconds between requests to the same host (default: 2.5)")
    scan.add_argument("--via", choices=["auto", "chrome", "jina", "browser"], default="auto",
                      help="How to fetch source pages. 'auto' (default): plain HTTP then a "
                           "local browser. 'chrome': drive your OWN running Chrome over CDP "
                           "— reuses its real session/bot-protection clearance for "
                           "autonomous scraping (launch Chrome with --remote-debugging-port "
                           "and browse the site once first). 'jina': fetch via Jina Reader. "
                           "'browser': force a fresh local browser.")
    scan.add_argument("--cdp-url", default="http://127.0.0.1:9222",
                      help="DevTools endpoint of your Chrome for --via chrome "
                           "(default: http://127.0.0.1:9222)")
    scan.add_argument("--show-browser", action="store_true",
                      help="Run the Playwright fallback with a visible browser window "
                           "instead of headless (passes bot checks more reliably)")
    scan.add_argument("--headless-compare", action="store_true",
                      help="Run the Google Shopping comparator headless. Default is a "
                           "visible window so its consent wall / any CAPTCHA is solvable.")
    _add_common(scan)

    hunt = sub.add_parser(
        "hunt",
        help="Auto-discover trending items (Amazon best sellers) and check them for "
             "retail-arbitrage profit — no search terms to type")
    hunt.add_argument("--source", action="append", choices=list(_SRC),
                      help="Retail site to hunt on (default: argos)")
    hunt.add_argument("--comparator", choices=["google", "ebay", "amazon"], default="ebay",
                      help="Resale price source to check margin against (default: ebay — "
                           "fast API, no browser). amazon/google drive a real browser.")
    hunt.add_argument("--limit", type=int, default=12,
                      help="Max trending items to consider (default: 12)")
    hunt.add_argument("--per-item", type=int, default=2,
                      help="Max retail products to take per trending item (default: 2)")
    hunt.add_argument("--ebay-env", choices=["PRODUCTION", "SANDBOX"], default="PRODUCTION")
    hunt.add_argument("--delay", type=float, default=2.5,
                      help="Minimum seconds between requests to the same host (default: 2.5)")
    hunt.add_argument("--via", choices=["auto", "chrome", "jina", "browser"], default="chrome",
                      help="How to fetch pages. 'chrome' (default): drive your own Chrome over "
                           "CDP — recommended, Amazon's best-seller pages block fresh browsers.")
    hunt.add_argument("--cdp-url", default="http://127.0.0.1:9222",
                      help="DevTools endpoint of your Chrome for --via chrome")
    hunt.add_argument("--show-browser", action="store_true")
    hunt.add_argument("--headless-compare", action="store_true")
    _add_common(hunt)

    demo = sub.add_parser("demo", help="Run the full pipeline on bundled fixtures (offline)")
    demo.add_argument("--comparator", choices=["both", "ebay", "pricerunner"], default="both",
                      help="Which comparator fixture(s) to run; 'both' (default) "
                           "produces the merged one-row-per-product report")
    _add_common(demo)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "demo":
        from .demo import demo_products, DemoEbayClient, DemoPriceRunnerClient
        products = demo_products()
        print(f"[demo] Parsed {len(products)} products from bundled Argos fixture "
              f"(same parser as live scans)\n")
        if args.comparator == "both":
            rows = compare_products_multi(
                products, [DemoPriceRunnerClient(), DemoEbayClient()],
                min_score=args.min_score, min_listings=args.min_listings,
            )
            _output_merged(rows, args)
        else:
            client = {"ebay": DemoEbayClient, "pricerunner": DemoPriceRunnerClient}[args.comparator]()
            comparisons = compare_products(
                products, client, min_score=args.min_score, min_listings=args.min_listings,
            )
            _output(comparisons, args)
        return 0

    if args.command == "hunt":
        return _hunt(args)

    # live scan
    wanted = ["pricerunner", "ebay"] if args.comparator == "both" else [args.comparator]
    client_id, client_secret, wanted, exit_code = _resolve_ebay_creds(wanted, args.comparator)
    if exit_code is not None:
        return exit_code

    from .browser import BrowserFetcher
    from .http import PoliteSession
    from .sources.base import ScrapeBlocked, SOURCES, make_scraper

    sources = args.source or ["argos"]
    products = []
    session = PoliteSession(min_delay=args.delay)

    if args.from_file:
        # Parse a page the user saved from their own (working) browser — the
        # reliable way past bot protection.
        from pathlib import Path
        name = sources[0]
        try:
            html = Path(args.from_file).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            print(f"Could not read {args.from_file}: {exc}", file=sys.stderr)
            return 1
        found = SOURCES[name].parse(html)
        for p in found:
            p.source = name
        if args.max_products:
            found = found[:args.max_products]
        print(f"Parsed {len(found)} products from {args.from_file} ({SOURCES[name].label}).")
        products.extend(found)
        if not products:
            print("No products found in that file — is it the search-results page, saved "
                  "as HTML? For a beta source, the parser may need adjusting "
                  "(scripts/inspect_dump.py <file>).", file=sys.stderr)
            return 1
    else:
        if not args.query:
            print("Give a search term (or a URL), or use --from-file.", file=sys.stderr)
            return 2
        fetch_mode = args.via
        if args.via == "chrome":
            # Start (or reuse) the user's real Chrome and drive it over CDP —
            # no manual terminal launch needed. Warm the first source's search
            # page so a first-ever run clears the bot check.
            from .chrome_launch import ensure_chrome
            from .sources.base import resolve_target
            warm = resolve_target(sources[0], args.query) if args.query else None
            ok, msg = ensure_chrome(args.cdp_url, open_url=warm)
            print(f"  {msg}")
            if not ok:
                return 1
            browser = BrowserFetcher(cdp_url=args.cdp_url, min_delay=args.delay)
            fetch_mode = "browser"  # force the browser path; it's the CDP one
        elif args.show_browser:
            browser = BrowserFetcher(headless=False, min_delay=args.delay)
        else:
            browser = None
        for name in sources:
            target = args.query
            pretty = target if target.startswith("http") else f'"{target}" on {SOURCES[name].label}'
            print(f"Searching {pretty} …")
            try:
                found = make_scraper(name, session, browser, fetch_mode=fetch_mode).scrape(
                    args.query, max_products=args.max_products)
                print(f"  {SOURCES[name].label}: {len(found)} products")
                products.extend(found)
            except ScrapeBlocked as exc:
                print(f"  {exc}", file=sys.stderr)
        if not products:
            print("No products scraped from any source.", file=sys.stderr)
            return 1

    cdp = args.cdp_url if args.via == "chrome" else None
    clients, closeables = _make_clients(wanted, args, session, cdp, client_id, client_secret)
    print(f"Scraped {len(products)} products total. Comparing on {', '.join(wanted)} …")

    try:
        if args.comparator == "both":
            rows = compare_products_multi(
                products, clients, min_score=args.min_score, min_listings=args.min_listings
            )
            _output_merged(rows, args)
        else:
            comparisons = compare_products(
                products, clients[0], min_score=args.min_score, min_listings=args.min_listings
            )
            _output(comparisons, args)
    finally:
        for c in closeables:
            c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
