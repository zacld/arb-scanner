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
    if not comparisons:
        print("No comparisons produced (no products scraped, or too few credible matches).")
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
    if not rows:
        print("No comparisons produced (no products scraped, or too few credible matches).")
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
    p.add_argument("--min-listings", type=int, default=None,
                   help="Minimum credible matches required (default: comparator's own — "
                        "3 for eBay listings, 1 for PriceRunner's aggregated products)")
    p.add_argument("--min-score", type=float, default=85.0,
                   help="Fuzzy title-match threshold 0-100 (default: 85)")
    p.add_argument("-v", "--verbose", action="store_true")


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
    scan.add_argument("--comparator", choices=["google", "both", "pricerunner", "ebay"],
                      default="google",
                      help="Price source. 'google' (default): Google Shopping via a real "
                           "browser, no API key — what it costs across retailers. 'ebay': "
                           "Browse API (needs EBAY_CLIENT_ID/EBAY_CLIENT_SECRET). "
                           "'pricerunner': its private API (currently unavailable). "
                           "'both': merge PriceRunner + eBay into one row per product.")
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

    # live scan
    wanted = ["pricerunner", "ebay"] if args.comparator == "both" else [args.comparator]
    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")
    if "ebay" in wanted and (not client_id or not client_secret):
        if args.comparator == "both":
            print(
                "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set — continuing with "
                "PriceRunner only (eBay columns will be empty).",
                file=sys.stderr,
            )
            wanted.remove("ebay")
        else:
            print(
                "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set.\n"
                "Register a (free) app at https://developer.ebay.com/my/keys and export both,\n"
                "use --comparator pricerunner (no key needed), or run "
                "`python -m arbfinder demo` to see the pipeline on fixture data.",
                file=sys.stderr,
            )
            return 2

    from .browser import BrowserFetcher
    from .http import PoliteSession
    from .sources.base import ScrapeBlocked, SOURCES, make_scraper

    sources = args.source or ["argos"]
    products = []

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
        session = PoliteSession(min_delay=args.delay)
        fetch_mode = args.via
        if args.via == "chrome":
            # Connect to the user's already-running, already-cleared Chrome.
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

    clients = []
    google_client = None
    for name in wanted:
        if name == "ebay":
            from .comparators.ebay import EbayBrowseClient
            clients.append(EbayBrowseClient(client_id, client_secret, env=args.ebay_env))
        elif name == "google":
            from .comparators.google_shopping import GoogleShoppingClient
            # Headed unless --headless-compare, so Google's consent wall / any
            # CAPTCHA can be handled in the visible window.
            google_client = GoogleShoppingClient(
                headless=args.headless_compare, min_delay=max(args.delay, 3.0)
            )
            clients.append(google_client)
        else:
            from .comparators.pricerunner import PriceRunnerClient
            # Shares the session so PriceRunner requests get the same politeness rules.
            clients.append(PriceRunnerClient(session))
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
        if google_client is not None:
            google_client.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
