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

from .pipeline import compare_products
from .report import format_table, sort_comparisons, write_csv


def _output(comparisons, args) -> None:
    comparisons = sort_comparisons(comparisons, by=args.sort)
    if args.min_diff_pct is not None:
        comparisons = [c for c in comparisons if c.diff_pct >= args.min_diff_pct]
    if not comparisons:
        print("No comparisons produced (no products scraped, or too few eBay matches).")
        return
    print(format_table(comparisons))
    path = write_csv(comparisons, args.out)
    print(f"\n{len(comparisons)} result(s) written to {path}")


def _add_common(p: argparse.ArgumentParser) -> None:
    p.add_argument("--out", default="results.csv", help="CSV output path (default: results.csv)")
    p.add_argument("--sort", choices=["abs", "pct"], default="abs",
                   help="Sort by £ gap (abs) or %% gap (pct)")
    p.add_argument("--min-diff-pct", type=float, default=None,
                   help="Only show rows where eBay is at least this %% above the retail price")
    p.add_argument("--min-listings", type=int, default=3,
                   help="Minimum credible eBay matches required (default: 3)")
    p.add_argument("--min-score", type=float, default=85.0,
                   help="Fuzzy title-match threshold 0-100 (default: 85)")
    p.add_argument("-v", "--verbose", action="store_true")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="arbfinder", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scrape a live Argos URL and compare against eBay UK")
    scan.add_argument("url", help="Argos search or category URL")
    scan.add_argument("--max-products", type=int, default=25)
    scan.add_argument("--fetch-ean", action="store_true",
                      help="Visit each product page to extract the EAN (slower, better matching)")
    scan.add_argument("--ebay-env", choices=["PRODUCTION", "SANDBOX"], default="PRODUCTION")
    scan.add_argument("--delay", type=float, default=2.5,
                      help="Minimum seconds between requests to the same host (default: 2.5)")
    _add_common(scan)

    demo = sub.add_parser("demo", help="Run the full pipeline on bundled fixtures (offline)")
    _add_common(demo)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if args.command == "demo":
        from .demo import demo_products, DemoEbayClient
        products = demo_products()
        print(f"[demo] Parsed {len(products)} products from bundled Argos fixture "
              f"(same parser as live scans)\n")
        comparisons = compare_products(
            products, DemoEbayClient(),
            min_score=args.min_score, min_listings=args.min_listings,
        )
        _output(comparisons, args)
        return 0

    # live scan
    client_id = os.environ.get("EBAY_CLIENT_ID")
    client_secret = os.environ.get("EBAY_CLIENT_SECRET")
    if not client_id or not client_secret:
        print(
            "EBAY_CLIENT_ID / EBAY_CLIENT_SECRET not set.\n"
            "Register a (free) app at https://developer.ebay.com/my/keys and export both,\n"
            "or run `python -m arbfinder demo` to see the pipeline on fixture data.",
            file=sys.stderr,
        )
        return 2

    from .comparators.ebay import EbayBrowseClient
    from .http import PoliteSession
    from .sources.argos import ArgosScraper

    session = PoliteSession(min_delay=args.delay)
    scraper = ArgosScraper(session)
    print(f"Scraping {args.url} …")
    products = scraper.scrape(args.url, max_products=args.max_products, fetch_ean=args.fetch_ean)
    print(f"Scraped {len(products)} products. Searching eBay UK …")
    ebay = EbayBrowseClient(client_id, client_secret, env=args.ebay_env)
    comparisons = compare_products(
        products, ebay, min_score=args.min_score, min_listings=args.min_listings
    )
    _output(comparisons, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
