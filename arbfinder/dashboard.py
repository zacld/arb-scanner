"""Local web dashboard for retail-arbitrage-finder.

Runs entirely on your own machine (binds to 127.0.0.1 only). Any eBay
credentials you enter are used in memory for that one scan and are NEVER
written to disk, logged, or sent anywhere except eBay's own OAuth/API
endpoints. Close the process and they're gone.

    pip install -r requirements.txt
    python -m arbfinder.dashboard
    # then open http://127.0.0.1:5000 in your browser

Note: live Argos scraping and the Google comparator open a real Chromium
window (Playwright) during the scan — that's expected. The eBay comparator
uses the official API and needs no browser.
"""

from __future__ import annotations

import html
import logging

try:
    from flask import Flask, request
except ImportError:  # pragma: no cover - guidance when Flask isn't installed
    raise SystemExit(
        "Flask is not installed. Run:  pip install -r requirements.txt\n"
        "(or: pip install flask)"
    )

from .pipeline import compare_products
from .report import sort_comparisons

log = logging.getLogger(__name__)
app = Flask(__name__)

PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>retail-arbitrage-finder</title>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font-family: system-ui, sans-serif; max-width: 1000px; margin: 2rem auto;
         padding: 0 1rem; line-height: 1.5; }}
 h1 {{ margin-bottom: .2rem; }}
 .sub {{ color: #888; margin-top: 0; }}
 form {{ display: grid; grid-template-columns: 1fr 1fr; gap: .8rem 1.2rem;
         background: rgba(127,127,127,.08); padding: 1.2rem; border-radius: 10px; }}
 label {{ display: flex; flex-direction: column; font-size: .85rem; font-weight: 600; }}
 label.wide {{ grid-column: 1 / -1; }}
 input, select {{ font-size: 1rem; padding: .45rem; margin-top: .2rem;
                  border: 1px solid #8888; border-radius: 6px; background: transparent; }}
 button {{ grid-column: 1 / -1; font-size: 1rem; font-weight: 700; padding: .7rem;
           border: 0; border-radius: 8px; background: #2d7; color: #062; cursor: pointer; }}
 .note {{ font-size: .8rem; color: #888; grid-column: 1 / -1; margin: 0; }}
 table {{ border-collapse: collapse; width: 100%; margin-top: 1.5rem; font-size: .9rem; }}
 th, td {{ text-align: right; padding: .45rem .6rem; border-bottom: 1px solid #8883; }}
 th:first-child, td:first-child {{ text-align: left; }}
 .pos {{ color: #2a2; }} .neg {{ color: #c44; }}
 .err {{ background: #c443; padding: .8rem 1rem; border-radius: 8px; }}
 a {{ color: #49f; }}
</style></head><body>
<h1>retail-arbitrage-finder</h1>
<p class="sub">Scrape a UK retail search page, compare prices elsewhere, surface the gaps.</p>
<form method="post" action="/scan">
 <label class="wide">Argos search / category URL
  <input name="url" placeholder="https://www.argos.co.uk/search/air-fryer/" value="{url}" required></label>
 <label>Max products
  <input name="max_products" type="number" min="1" max="60" value="{max_products}"></label>
 <label>Compare against
  <select name="comparator">
   <option value="google" {g_sel}>Google Shopping (no key)</option>
   <option value="ebay" {e_sel}>eBay UK (needs key)</option>
  </select></label>
 <label>Selling fees %
  <input name="fees" type="number" step="0.5" value="{fees}"></label>
 <label>Postage £
  <input name="postage" type="number" step="0.01" value="{postage}"></label>
 <label>eBay Client ID <span style="font-weight:400">(App ID — only for eBay)</span>
  <input name="ebay_id" autocomplete="off" value="{ebay_id}"></label>
 <label>eBay Client Secret <span style="font-weight:400">(Cert ID — stays on this machine)</span>
  <input name="ebay_secret" type="password" autocomplete="off" placeholder="{secret_ph}"></label>
 <p class="note">Runs locally on 127.0.0.1. Credentials are used only for this scan —
    never saved, logged, or sent anywhere but eBay. A Chromium window may open for
    scraping; that's expected.</p>
 <button type="submit">Run scan</button>
</form>
{results}
</body></html>"""


def _fmt_signed(v: float | None) -> str:
    if v is None:
        return '<td>—</td>'
    cls = "pos" if v > 0 else ("neg" if v < 0 else "")
    return f'<td class="{cls}">{v:+.2f}</td>'


def _results_table(comparisons, market: str, fees: float, postage: float) -> str:
    if not comparisons:
        return ('<p class="err">No comparisons produced — no products scraped, too few '
                'credible matches, or the comparison source blocked the request. '
                'See the terminal for details.</p>')
    head = (f"<tr><th>Product</th><th>Argos £</th><th>{html.escape(market)} £</th>"
            "<th>Diff £</th><th>Diff %</th><th>Net £</th><th>N</th>"
            "<th>Match</th><th>Seller</th></tr>")
    rows = []
    for c in comparisons:
        net = c.net_profit(fees, postage)
        seller = ""
        if c.listings and getattr(c.listings[0], "seller", ""):
            seller = c.listings[0].seller
        name = html.escape(c.product.name)
        link = f'<a href="{html.escape(c.product.url)}" target="_blank" rel="noopener">{name}</a>'
        mlink = (f'<a href="{html.escape(c.market_url)}" target="_blank" rel="noopener">'
                 f'{c.market_price:.2f}</a>' if c.market_url else f"{c.market_price:.2f}")
        rows.append(
            f"<tr><td>{link}</td><td>{c.product.price:.2f}</td><td>{mlink}</td>"
            f"{_fmt_signed(c.diff_abs)}{_fmt_signed(c.diff_pct)}{_fmt_signed(net)}"
            f"<td>{c.n_listings}</td><td>{html.escape(c.matched_by)}</td>"
            f"<td>{html.escape(seller)}</td></tr>"
        )
    note = ("Net £ = eBay resale minus fees + postage."
            if market == "ebay" else
            "Net £ is N/A for retail comparisons (prices are asks, not resale value).")
    return (f"<table>{head}{''.join(rows)}</table>"
            f'<p class="note">{note} {len(comparisons)} result(s), biggest gap first.</p>')


def _run_scan(form) -> str:
    url = form.get("url", "").strip()
    comparator = form.get("comparator", "google")
    try:
        max_products = int(form.get("max_products") or 10)
        fees = float(form.get("fees") or 13)
        postage = float(form.get("postage") or 0)
    except ValueError:
        return '<p class="err">Max products, fees and postage must be numbers.</p>'
    if not url:
        return '<p class="err">Please enter an Argos URL.</p>'

    from .browser import BrowserFetcher
    from .http import PoliteSession
    from .sources.argos import ArgosScraper, ScrapeBlocked

    if comparator == "ebay":
        cid = form.get("ebay_id", "").strip()
        secret = form.get("ebay_secret", "").strip()
        if not cid or not secret:
            return '<p class="err">eBay needs both a Client ID and Client Secret.</p>'
        from .comparators.ebay import EbayBrowseClient
        client = EbayBrowseClient(cid, secret)
        google = None
    else:
        from .comparators.google_shopping import GoogleShoppingClient
        client = google = GoogleShoppingClient(headless=False, interactive=False)

    session = PoliteSession()
    scraper = ArgosScraper(session, browser=BrowserFetcher(headless=False))
    try:
        try:
            products = scraper.scrape(url, max_products=max_products)
        except ScrapeBlocked as exc:
            return f'<p class="err">{html.escape(str(exc))}</p>'
        comparisons = sort_comparisons(
            compare_products(products, client), fees_pct=fees, postage=postage
        )
        market = getattr(client, "market_name", comparator)
        return _results_table(comparisons, market, fees, postage)
    except Exception as exc:  # noqa: BLE001 - surface any failure in the page
        log.exception("scan failed")
        return f'<p class="err">Scan failed: {html.escape(str(exc))}</p>'
    finally:
        if google is not None:
            google.close()


def _render(results: str = "", form=None) -> str:
    form = form or {}
    comparator = form.get("comparator", "google")
    return PAGE.format(
        url=html.escape(form.get("url", "")),
        max_products=html.escape(str(form.get("max_products", "10"))),
        fees=html.escape(str(form.get("fees", "13"))),
        postage=html.escape(str(form.get("postage", "0"))),
        ebay_id=html.escape(form.get("ebay_id", "")),
        secret_ph="entered secret not shown" if form.get("ebay_secret") else "",
        g_sel="selected" if comparator != "ebay" else "",
        e_sel="selected" if comparator == "ebay" else "",
        results=results,
    )


@app.route("/")
def index() -> str:
    return _render()


@app.route("/scan", methods=["POST"])
def scan() -> str:
    return _render(results=_run_scan(request.form), form=request.form)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    print("retail-arbitrage-finder dashboard → http://127.0.0.1:5000  (Ctrl+C to stop)")
    # threaded=False so Playwright's sync API stays on one thread.
    app.run(host="127.0.0.1", port=5000, threaded=False)


if __name__ == "__main__":
    main()
