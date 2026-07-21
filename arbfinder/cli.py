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

    scan = sub.add_parser("scan", help="Scrape a live Argos URL and compare against a marketplace")
    scan.add_argument("url", help="Argos search or category URL")
    scan.add_argument("--comparator", choices=["both", "pricerunner", "ebay"], default="both",
                      help="Which price source(s) to check. Default 'both' merges "
                           "PriceRunner and eBay into one row per product; name one "
                           "to run it alone. ebay needs EBAY_CLIENT_ID/EBAY_CLIENT_SECRET "
                           "(missing creds in 'both' mode just drops the eBay columns)")
    scan.add_argument("--max-products", type=int, default=25)
    scan.add_argument("--fetch-ean", action="store_true",
                      help="Visit each product page to extract the EAN (slower, better matching)")
    scan.add_argument("--ebay-env", choices=["PRODUCTION", "SANDBOX"], default="PRODUCTION")
    scan.add_argument("--delay", type=float, default=2.5,
                      help="Minimum seconds between requests to the same host (default: 2.5)")
    scan.add_argument("--show-browser", action="store_true",
                      help="Run the Playwright fallback with a visible browser window "
                           "instead of headless (passes bot checks more reliably)")
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
    from .sources.argos import ArgosScraper, ScrapeBlocked

    session = PoliteSession(min_delay=args.delay)
    browser = BrowserFetcher(headless=not args.show_browser,
                             min_delay=args.delay) if args.show_browser else None
    scraper = ArgosScraper(session, browser=browser)
    print(f"Scraping {args.url} …")
    try:
        products = scraper.scrape(args.url, max_products=args.max_products, fetch_ean=args.fetch_ean)
    except ScrapeBlocked as exc:
        print(f"\n{exc}", file=sys.stderr)
        return 1

    clients = []
    for name in wanted:
        if name == "ebay":
            from .comparators.ebay import EbayBrowseClient
            clients.append(EbayBrowseClient(client_id, client_secret, env=args.ebay_env))
        else:
            from .comparators.pricerunner import PriceRunnerClient
            # Shares the session so PriceRunner requests get the same politeness rules.
            clients.append(PriceRunnerClient(session))
    print(f"Scraped {len(products)} products. Searching {', '.join(wanted)} …")

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
    return 0


if __name__ == "__main__":
    sys.exit(main())
