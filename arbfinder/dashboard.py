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
import os
import subprocess
import sys
from pathlib import Path

try:
    from flask import Flask, redirect, request
except ImportError:  # pragma: no cover - guidance when Flask isn't installed
    raise SystemExit(
        "Flask is not installed. Run:  pip install -r requirements.txt\n"
        "(or: pip install flask)"
    )

from . import config
from .pipeline import compare_products
from .report import sort_comparisons
from .sources.base import SOURCES, ScrapeBlocked, make_scraper

log = logging.getLogger(__name__)
app = Flask(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent   # dir containing arbfinder/
BRANCH = "claude/retail-arbitrage-finder-tjc9k2"


def _git(gitargs, timeout: float = 90):
    """Run git in the repo; return (returncode, combined output)."""
    try:
        r = subprocess.run(["git", *gitargs], cwd=REPO_ROOT, capture_output=True,
                           text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except Exception as exc:  # noqa: BLE001 - git missing / not a repo / timeout
        return 1, str(exc)


def _version() -> str:
    code, out = _git(["log", "-1", "--format=%h %s"])
    return out[:64] if code == 0 and out else "unknown"


def _restart() -> None:
    """Replace this process with a fresh one so pulled code takes effect."""
    try:
        os.chdir(REPO_ROOT)
        os.execv(sys.executable, [sys.executable, "-m", "arbfinder.dashboard"])
    except Exception:  # noqa: BLE001
        os._exit(1)

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
<form method="post" action="/scan" enctype="multipart/form-data">
 <label class="wide">① Search term — type a product and scan it live
  <input name="url" placeholder="air fryer" value="{url}"></label>
 <label class="wide" style="flex-direction:row;align-items:center;gap:.5rem;font-weight:600">
  <input type="checkbox" name="hunt" value="1" {hunt_chk} style="width:auto">
  🔥 Or hunt trending — auto-pick hot items from Amazon best sellers &amp; movers
  (ignores the search box; needs “My Chrome”)</label>
 <label>Fetch via
  <select name="fetch_mode">
   <option value="chrome" {fm_chrome}>My Chrome (autonomous — recommended)</option>
   <option value="browser" {fm_browser}>Fresh browser (often blocked by Akamai)</option>
  </select></label>
 <label>My Chrome debug URL <span style="font-weight:400">(for “My Chrome”)</span>
  <input name="cdp_url" value="{cdp_url}"></label>
 <p class="note">“My Chrome” launches a real Chrome for you automatically when you
    scan (a dedicated window, separate from your normal browsing) and reuses it
    after that — no terminal, no file to save. Status: {chrome_status}
    · <a href="/chrome">start / re-check now</a>. If Argos shows a challenge in
    that window the first time, solve it once and scan again.</p>
 <label class="wide">… or upload a saved search page (HTML) instead
  <span style="font-weight:400">— always-works fallback: open a retailer's results page,
   save it (Chrome: Cmd+S → "Webpage, HTML Only" · Safari: "Page Source"), choose it here.</span>
  <input type="file" name="page" accept=".html,.htm,text/html"></label>
 <label>Which retailer?
  <select name="source">{source_options}</select></label>
 <label>Compare against
  <select name="comparator">
   <option value="ebay" {e_sel}>eBay UK — needs key, gives resale + Net £</option>
   <option value="amazon" {a_sel}>Amazon UK — no key, resale + Net £ (uses your Chrome)</option>
   <option value="google" {g_sel}>Google Shopping — no key, opens a browser</option>
  </select></label>
 <label>Max products
  <input name="max_products" type="number" min="1" max="60" value="{max_products}"></label>
 <label>eBay environment
  <select name="ebay_env">
   <option value="PRODUCTION" {prd_sel}>Production (real data)</option>
   <option value="SANDBOX" {sbx_sel}>Sandbox (test — no real listings)</option>
  </select></label>
 <label>Selling fees %
  <input name="fees" type="number" step="0.5" value="{fees}"></label>
 <label>Postage £ <span style="font-weight:400">(bulky items ~£6–10)</span>
  <input name="postage" type="number" step="0.01" value="{postage}"></label>
 <label>Min net £ <span style="font-weight:400">(blank = show all · 0 = anything profitable)</span>
  <input name="min_net" type="number" step="0.01" value="{min_net}"></label>
 <label>eBay Client ID <span style="font-weight:400">(App ID — only for eBay)</span>
  <input name="ebay_id" autocomplete="off" value="{ebay_id}"></label>
 <label>eBay Client Secret <span style="font-weight:400">(Cert ID — stays on this machine)</span>
  <input name="ebay_secret" type="password" autocomplete="off" placeholder="{secret_ph}"></label>
 <label class="wide" style="flex-direction:row;align-items:center;gap:.5rem;font-weight:400">
  <input type="checkbox" name="remember" value="1" {remember_chk} style="width:auto">
  Remember these on this machine (saved unencrypted to {config_path})</label>
 <p class="note">Runs locally on 127.0.0.1. Credentials are sent only to eBay's API.
    {saved_note}</p>
 <button type="submit">Run scan</button>
</form>
{results}
<p class="note" style="text-align:center;margin-top:2.5rem;border-top:1px solid #8883;padding-top:1rem">
 Version <code>{version}</code> · <a href="/update">⟳ Update to latest &amp; restart</a></p>
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
    head = (f"<tr><th>Product</th><th>Source</th><th>Price £</th><th>{html.escape(market)} £</th>"
            "<th>Diff £</th><th>Diff %</th><th>Net £</th><th>N</th>"
            "<th>Match</th><th>Seller</th></tr>")
    rows = []
    for c in comparisons:
        net = c.net_profit(fees, postage)
        seller = ""
        if c.listings and getattr(c.listings[0], "seller", ""):
            seller = c.listings[0].seller
        src = SOURCES[c.product.source].label if c.product.source in SOURCES else c.product.source
        name = html.escape(c.product.name)
        link = f'<a href="{html.escape(c.product.url)}" target="_blank" rel="noopener">{name}</a>'
        mlink = (f'<a href="{html.escape(c.market_url)}" target="_blank" rel="noopener">'
                 f'{c.market_price:.2f}</a>' if c.market_url else f"{c.market_price:.2f}")
        rows.append(
            f"<tr><td>{link}</td><td>{html.escape(src)}</td><td>{c.product.price:.2f}</td>"
            f"<td>{mlink}</td>"
            f"{_fmt_signed(c.diff_abs)}{_fmt_signed(c.diff_pct)}{_fmt_signed(net)}"
            f"<td>{c.n_listings}</td><td>{html.escape(c.matched_by)}</td>"
            f"<td>{html.escape(seller)}</td></tr>"
        )
    note = (f"Net £ = {market} resale minus fees + postage."
            if market in ("ebay", "amazon") else
            "Net £ is N/A for retail comparisons (prices are asks, not resale value).")
    return (f"<table>{head}{''.join(rows)}</table>"
            f'<p class="note">{note} {len(comparisons)} result(s), biggest gap first.</p>')


def _run_scan(form, files=None) -> str:
    files = files or {}
    query = form.get("url", "").strip()
    comparator = form.get("comparator", "ebay")
    source = form.get("source") if form.get("source") in SOURCES else "argos"
    fetch_mode = form.get("fetch_mode") or "chrome"
    cdp_url = form.get("cdp_url", "").strip() or "http://127.0.0.1:9222"
    hunt = bool(form.get("hunt"))
    try:
        max_products = int(form.get("max_products") or 10)
        fees = float(form.get("fees") or 13)
        postage = float(form.get("postage") or 0)
    except ValueError:
        return '<p class="err">Max products, fees and postage must be numbers.</p>'
    min_net = None
    raw_min_net = form.get("min_net", "").strip()
    if raw_min_net:
        try:
            min_net = float(raw_min_net)
        except ValueError:
            return '<p class="err">Min net £ must be a number (or left blank).</p>'

    upload = files.get("page")
    upload_html = ""
    if upload is not None and getattr(upload, "filename", ""):
        try:
            upload_html = upload.read().decode("utf-8", errors="replace")
        except Exception as exc:  # noqa: BLE001
            return f'<p class="err">Could not read the uploaded file: {html.escape(str(exc))}</p>'
    if not hunt and not upload_html and not query:
        return ('<p class="err">Enter a search term to scan live (recommended), '
                'tick “hunt trending”, or upload a saved search page.</p>')

    # -- build the comparator ------------------------------------------------
    closeable = None
    if comparator == "ebay":
        saved = config.load_credentials()
        cid = form.get("ebay_id", "").strip() or saved["ebay_client_id"]
        secret = form.get("ebay_secret", "").strip() or saved["ebay_client_secret"]
        if not cid or not secret:
            return ('<p class="err">eBay needs both a Client ID and Client Secret '
                    '(enter them above, or save them once with "Remember").</p>')
        if form.get("remember"):
            config.save_credentials(cid, secret)
        from .comparators.ebay import EbayBrowseClient
        env = "SANDBOX" if form.get("ebay_env") == "SANDBOX" else "PRODUCTION"
        client = EbayBrowseClient(cid, secret, env=env)
    elif comparator == "amazon":
        from .comparators.amazon import AmazonClient
        # Reuse the user's Chrome over CDP when they're scraping that way, so
        # Amazon sees the same real, cleared session.
        client = AmazonClient(cdp_url=(cdp_url if fetch_mode == "chrome" else None),
                              headless=False, interactive=False)
        closeable = client
    else:
        from .comparators.google_shopping import GoogleShoppingClient
        client = GoogleShoppingClient(headless=False, interactive=False)
        closeable = client

    try:
        # -- gather products: hunt / uploaded page / live scrape -------------
        if hunt:
            from .browser import BrowserFetcher
            from .chrome_launch import ensure_chrome
            from .discover import discover_bestsellers
            from .http import PoliteSession
            if fetch_mode == "chrome":
                ok, chrome_msg = ensure_chrome(
                    cdp_url, open_url="https://www.amazon.co.uk/gp/movers-and-shakers")
                if not ok:
                    return f'<p class="err">{html.escape(chrome_msg)}</p>'
                fetcher = BrowserFetcher(cdp_url=cdp_url)
                sc_mode = "browser"
            else:
                fetcher = BrowserFetcher(headless=False)
                sc_mode = "auto"
            try:
                ideas = discover_bestsellers(fetcher, limit=min(max_products, 15))
                products = []
                scraper = make_scraper(source, PoliteSession(), fetcher, fetch_mode=sc_mode)
                for idea in ideas:
                    try:
                        products.extend(scraper.scrape(idea.term, max_products=2))
                    except ScrapeBlocked:
                        pass
            finally:
                try:
                    fetcher.close()
                except Exception:  # noqa: BLE001
                    pass
            if not ideas:
                return ('<p class="err">Couldn\'t read Amazon\'s best-seller pages — make '
                        'sure “My Chrome” is running and you\'ve visited amazon.co.uk once '
                        'in that window (solve any robot check), then try again.</p>')
            note = (f"Discovered {len(ideas)} trending items on Amazon, found "
                    f"{len(products)} at {SOURCES[source].label}")
        elif upload_html:
            products = SOURCES[source].parse(upload_html)
            for p in products:
                p.source = source
            if max_products:
                products = products[:max_products]
            note = f"Parsed {len(products)} products from the uploaded {SOURCES[source].label} page"
            if not products:
                return ('<p class="err">No products found in that file. Make sure it is the '
                        'search-<em>results</em> page saved as HTML, and that the retailer '
                        f'matches (“{html.escape(SOURCES[source].label)}” selected).</p>')
        else:
            from .browser import BrowserFetcher
            from .http import PoliteSession
            if fetch_mode == "chrome":
                # Drive the user's own Chrome over CDP — the autonomous route
                # past Akamai. Start (or reuse) it for them so there's no
                # terminal step: open the retailer page first to warm the
                # bot-protection clearance, then force the browser fetch path.
                from .chrome_launch import ensure_chrome
                from .sources.base import resolve_target
                ok, chrome_msg = ensure_chrome(cdp_url, open_url=resolve_target(source, query))
                if not ok:
                    return f'<p class="err">{html.escape(chrome_msg)}</p>'
                fetcher = BrowserFetcher(cdp_url=cdp_url)
                scraper = make_scraper(source, PoliteSession(), fetcher, fetch_mode="browser")
                via = f"your Chrome ({chrome_msg.rstrip('.').lower()})"
            else:
                fetcher = BrowserFetcher(headless=False)
                scraper = make_scraper(source, PoliteSession(), fetcher)
                via = "a fresh browser"
            try:
                products = scraper.scrape(query, max_products=max_products)
            except ScrapeBlocked as exc:
                hint = (
                    'For “My Chrome”: launch it with the debug port '
                    '(<code>scripts/chrome-debug.command</code>) and browse the retailer '
                    'once in that window first. '
                    if fetch_mode == "chrome" else
                    'A fresh browser is usually blocked — switch “Fetch via” to “My Chrome”, '
                    'or save the page and upload it. '
                )
                return (f'<p class="err">{html.escape(str(exc).splitlines()[-1])}<br><br>'
                        f'{hint}</p>')
            finally:
                try:
                    fetcher.close()
                except Exception:  # noqa: BLE001 - teardown is best-effort
                    pass
            note = f"Scraped {len(products)} products live from {SOURCES[source].label} via {via}"

        comparisons = sort_comparisons(
            compare_products(products, client), fees_pct=fees, postage=postage
        )
        if comparator == "amazon" and getattr(client, "blocked", False):
            return ('<p class="err">Amazon showed a robot check, so it returned no prices. '
                    'A Chrome tab is now open at Amazon — <b>solve the check in that window</b> '
                    '(tick the box / type the characters until you can see search results), '
                    'then hit <b>Run scan</b> again. It stays cleared after the first time.<br><br>'
                    'This only happens on the first Amazon visit in that Chrome profile.</p>')
        prefix = f'<p class="note">{note}.</p>'
        if min_net is not None:
            kept = [c for c in comparisons
                    if (n := c.net_profit(fees, postage)) is not None and n >= min_net]
            if comparisons and not kept:
                return (prefix + f'<p class="err">None of the {len(comparisons)} matched '
                        f'product(s) clear a net profit of £{min_net:.2f} after {fees:g}% fees '
                        f'+ £{postage:.2f} postage. Lower the threshold or postage.</p>')
            comparisons = kept
            prefix += f'<p class="note">Showing only rows with net ≥ £{min_net:.2f}.</p>'
        market = getattr(client, "market_name", comparator)
        return prefix + _results_table(comparisons, market, fees, postage)
    except Exception as exc:  # noqa: BLE001 - surface any failure in the page
        log.exception("scan failed")
        return f'<p class="err">Scan failed: {html.escape(str(exc))}</p>'
    finally:
        if closeable is not None:
            closeable.close()


def _source_options(form) -> str:
    selected = form.get("source") if form.get("source") in SOURCES else "argos"
    opts = []
    for name, src in SOURCES.items():
        tag = "" if src.verified else " (beta)"
        sel = "selected" if name == selected else ""
        opts.append(f'<option value="{name}" {sel}>{html.escape(src.label)}{tag}</option>')
    return "".join(opts)


def _render(results: str = "", form=None) -> str:
    form = form or {}
    comparator = form.get("comparator", "ebay")
    env = form.get("ebay_env", "PRODUCTION")
    fetch_mode = form.get("fetch_mode", "chrome")
    cdp_url = form.get("cdp_url") or "http://127.0.0.1:9222"
    from .chrome_launch import is_running
    chrome_status = ("<b style='color:#2a2'>running</b>" if is_running(cdp_url, timeout=0.5)
                     else "<b style='color:#c44'>not started</b> (starts on scan)")
    saved = config.load_credentials()
    have_secret = bool(saved["ebay_client_secret"]) or bool(form.get("ebay_secret"))
    saved_note = (
        f'A saved secret is in use — <a href="/forget">forget it</a>. '
        if config.has_saved_secret() else
        "Not saved unless you tick “Remember”."
    )
    return PAGE.format(
        url=html.escape(form.get("url", "")),
        source_options=_source_options(form),
        max_products=html.escape(str(form.get("max_products", "10"))),
        fees=html.escape(str(form.get("fees", "13"))),
        postage=html.escape(str(form.get("postage", "0"))),
        min_net=html.escape(str(form.get("min_net", ""))),
        cdp_url=html.escape(cdp_url),
        chrome_status=chrome_status,
        hunt_chk="checked" if form.get("hunt") else "",
        fm_chrome="selected" if fetch_mode != "browser" else "",
        fm_browser="selected" if fetch_mode == "browser" else "",
        # Pre-fill the Client ID from saved/env; never pre-fill the secret field.
        ebay_id=html.escape(form.get("ebay_id") or saved["ebay_client_id"]),
        secret_ph="saved secret will be used — leave blank" if have_secret else "",
        remember_chk="checked" if (form.get("remember") or config.has_saved_secret()) else "",
        config_path=html.escape(str(config.CONFIG_PATH)),
        saved_note=saved_note,
        version=html.escape(_version()),
        g_sel="selected" if comparator == "google" else "",
        e_sel="selected" if comparator == "ebay" else "",
        a_sel="selected" if comparator == "amazon" else "",
        prd_sel="selected" if env != "SANDBOX" else "",
        sbx_sel="selected" if env == "SANDBOX" else "",
        results=results,
    )


@app.route("/")
def index() -> str:
    return _render()


@app.route("/scan", methods=["POST"])
def scan() -> str:
    return _render(results=_run_scan(request.form, request.files), form=request.form)


@app.route("/chrome")
def chrome():
    from .chrome_launch import ensure_chrome
    ok, msg = ensure_chrome("http://127.0.0.1:9222")
    cls = "note" if ok else "err"
    return _render(results=f'<p class="{cls}">{html.escape(msg)}</p>')


@app.route("/update")
def update():
    code, out = _git(["pull", "origin", BRANCH])
    already = code == 0 and ("up to date" in out.lower() or "up-to-date" in out.lower())
    if code != 0:
        body = ('<p class="err">Update failed — pull it manually if this persists.'
                f'<br><pre style="white-space:pre-wrap">{html.escape(out)}</pre></p>')
    elif already:
        body = ('<p class="note">You\'re already on the latest version.'
                f'<br><pre style="white-space:pre-wrap">{html.escape(out)}</pre></p>')
    else:
        import threading
        threading.Timer(1.5, _restart).start()
        body = ('<p class="note"><b>Updated!</b> Restarting to apply the new code…'
                f'<br><pre style="white-space:pre-wrap">{html.escape(out)}</pre>'
                'Wait ~5 seconds, then <a href="/">refresh this page</a>.</p>')
    return _render(results=body)


@app.route("/forget")
def forget():
    removed = config.clear_credentials()
    msg = "Saved credentials removed." if removed else "No saved credentials to remove."
    return _render(results=f'<p class="note">{msg}</p>')


def _free_port(start: int, tries: int = 20) -> int:
    """First bindable port at/after ``start`` (macOS AirPlay squats on 5000)."""
    import socket
    for offset in range(tries):
        port = start + offset
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    return start


def main() -> None:
    import os
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    requested = int(os.environ.get("ARBFINDER_PORT", "5000"))
    port = _free_port(requested)
    # Pin the chosen port so an in-app "Update & restart" re-binds the same URL.
    os.environ["ARBFINDER_PORT"] = str(port)
    url = f"http://127.0.0.1:{port}"
    if port != requested:
        print(f"Port {requested} was busy (often macOS AirPlay Receiver, which makes the "
              f"browser DOWNLOAD the page instead of showing it) — using {port} instead.")
    print(f"retail-arbitrage-finder dashboard → {url}  (Ctrl+C to stop)")
    # Open the right URL automatically so there's no chance of hitting AirPlay
    # on :5000 by mistake. Unless disabled, and best-effort only.
    if os.environ.get("ARBFINDER_NO_BROWSER") != "1":
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # threaded=False so Playwright's sync API stays on one thread.
    app.run(host="127.0.0.1", port=port, threaded=False)


if __name__ == "__main__":
    main()
