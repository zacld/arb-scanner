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

import hmac
import html
import json
import logging
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

try:
    from flask import Flask, jsonify, redirect, request, session
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
# Signing key for the login session cookie (set a stable one in hosted deploys).
app.secret_key = os.environ.get("ARBFINDER_SECRET_KEY") or secrets.token_hex(16)

REPO_ROOT = Path(__file__).resolve().parent.parent   # dir containing arbfinder/
BRANCH = "claude/retail-arbitrage-finder-tjc9k2"
DATA_DIR = Path(os.environ.get("ARBFINDER_DATA_DIR", str(REPO_ROOT)))
RESULTS_PATH = DATA_DIR / "results.csv"

# Hosted mode (set on the server): bind public, require login, hide local-only
# controls (My Chrome / eBay secret), force server-side Chromium.
HOSTED = os.environ.get("ARBFINDER_HOSTED") == "1"


# -- background job store (lazy so importing the module has no side effects) --
import threading as _threading

_STORE = None
_STORE_LOCK = _threading.Lock()


class _MemUpload:
    """Minimal uploaded-file stand-in so a queued job can carry a saved page."""

    def __init__(self, filename: str, data: bytes):
        self.filename = filename
        self._data = data

    def read(self) -> bytes:
        return self._data


def run_job_local(params, progress):
    """Run one scan in-process (local Chrome + eBay) → (result_html, csv_path).

    Used by both the local in-process worker and the Mac agent (which imports
    this to run hosted-site jobs on the Mac's residential IP).
    """
    form = dict(params)
    files = {}
    up = form.pop("_upload_html", None)
    if up:
        files["page"] = _MemUpload("upload.html", up.encode("utf-8"))
    result_html = _run_scan(form, files, progress=progress)
    return result_html, (str(RESULTS_PATH) if RESULTS_PATH.exists() else "")


def _job_runner(job_id, params, progress):
    return run_job_local(params, progress)


def store():
    global _STORE
    with _STORE_LOCK:
        if _STORE is None:
            from .jobs import JobStore
            _STORE = JobStore(DATA_DIR / "arbfinder-jobs.db")
            # Local mode runs jobs in-process. Hosted (broker) mode leaves them
            # queued for the Mac agent — the server can't scrape retail sites.
            if not HOSTED:
                _STORE.start_worker(_job_runner)
        return _STORE


# -- password gate (only active when ARBFINDER_PASSWORD is set) ---------------

def _auth_on() -> bool:
    return bool(os.environ.get("ARBFINDER_PASSWORD"))


@app.before_request
def _require_login():
    if not _auth_on():
        return None
    # /agent/* has its own token auth; /login and static are always open.
    if (request.path == "/login" or request.path.startswith("/static")
            or request.path.startswith("/agent/")):
        return None
    if session.get("authed"):
        return None
    return redirect("/login")


# -- Mac-agent broker API (token-gated, separate from the login) --------------

def _agent_ok() -> bool:
    token = os.environ.get("ARBFINDER_AGENT_TOKEN")
    if not token:
        return False
    return hmac.compare_digest(request.headers.get("X-Agent-Token", ""), token)


@app.route("/agent/next")
def agent_next():
    if not _agent_ok():
        return ("forbidden", 403)
    st = store()
    st.mark_agent_seen()
    job = st.claim_next()
    if not job:
        return ("", 204)
    return jsonify(job)


@app.route("/agent/progress/<job_id>", methods=["POST"])
def agent_progress(job_id):
    if not _agent_ok():
        return ("forbidden", 403)
    data = request.get_json(silent=True) or {}
    store().update(job_id, status="running", stage=data.get("stage", ""),
                   detail=data.get("detail", ""))
    return ("", 204)


@app.route("/agent/result/<job_id>", methods=["POST"])
def agent_result(job_id):
    if not _agent_ok():
        return ("forbidden", 403)
    data = request.get_json(silent=True) or {}
    if data.get("status") == "error":
        store().update(job_id, status="error", error=data.get("error", ""))
    else:
        store().update(job_id, status="done", stage="done", detail="",
                       result_html=data.get("result_html", ""), csv_path="")
    return ("", 204)

SIGNAL_FIELDS = {"movers": "sig_movers", "bestsellers": "sig_bestsellers",
                 "seasonal": "sig_seasonal", "manual": "sig_manual"}
DEFAULT_SIGNALS = ["movers", "seasonal", "manual"]  # best sellers off by default


def _opt_pct(raw: str):
    """Parse a percentage-style field ('15') to a fraction (0.15); None if blank."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:
        return float(raw) / 100.0
    except ValueError:
        return None


def _thresholds_label(min_net, min_roi, min_match) -> str:
    """Human phrase for the active profit gates, for the near-miss banner."""
    parts = []
    if min_net is not None:
        parts.append(f"£{min_net:.2f} net")
    if min_roi is not None:
        parts.append(f"{min_roi * 100:.0f}% ROI")
    if min_match is not None:
        parts.append(f"{min_match * 100:.0f}% match")
    return " / ".join(parts) if parts else "profit gates"


def _sig_chk(form, name: str, default: bool) -> str:
    """Checkbox state: reflect the submitted form, else the initial default."""
    if form:  # a submitted form (POST) — checkbox present only if it was ticked
        return "checked" if form.get(SIGNAL_FIELDS[name]) else ""
    return "checked" if default else ""


# Default profit gates for a fresh page: direct profitability filters (NOT
# assumptions about brand/price/category). Pre-filled, fully editable, and
# bypassable with "Show all results".
DEFAULT_MIN_NET = "5"
DEFAULT_MIN_ROI = "15"
DEFAULT_MIN_MATCH = "80"


def _prefill(form, key: str, default: str) -> str:
    """A number field's value: submitted value on POST, default on a fresh GET."""
    return html.escape(str(form.get(key, ""))) if form else default


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
 table {{ display: block; overflow-x: auto; }}  /* mobile: scroll wide tables */
 {local_only_css}
</style></head><body>
<h1>retail-arbitrage-finder</h1>
<p class="sub">Scrape a UK retail search page, compare prices elsewhere, surface the gaps.</p>
{agent_status}
<form method="post" action="/scan" enctype="multipart/form-data">
 <label class="wide">① Search term — type a product and scan it live
  <input name="url" placeholder="air fryer" value="{url}"></label>
 <label class="wide" style="flex-direction:row;align-items:center;gap:.5rem;font-weight:600">
  <input type="checkbox" name="hunt" value="1" {hunt_chk} style="width:auto">
  🔥 Or hunt trending — auto-pick categories from demand signals, rank by profit
  (ignores the search box)</label>
 <div class="wide" style="background:rgba(127,127,127,.06);border-radius:8px;padding:.6rem .8rem">
  <div style="font-size:.85rem;font-weight:600;margin-bottom:.3rem">Hunt signals</div>
  <div style="display:flex;flex-wrap:wrap;gap:.4rem 1.1rem;font-weight:400;font-size:.85rem">
   <label style="flex-direction:row;align-items:center;gap:.35rem;font-weight:400">
    <input type="checkbox" name="sig_movers" value="1" {sig_movers} style="width:auto">
    Amazon Movers &amp; Shakers</label>
   <label style="flex-direction:row;align-items:center;gap:.35rem;font-weight:400">
    <input type="checkbox" name="sig_bestsellers" value="1" {sig_bestsellers} style="width:auto">
    Amazon Best Sellers <span style="color:#888">(noisier)</span></label>
   <label style="flex-direction:row;align-items:center;gap:.35rem;font-weight:400">
    <input type="checkbox" name="sig_seasonal" value="1" {sig_seasonal} style="width:auto">
    Seasonal calendar</label>
   <label style="flex-direction:row;align-items:center;gap:.35rem;font-weight:400">
    <input type="checkbox" name="sig_manual" value="1" {sig_manual} style="width:auto">
    Manual</label>
  </div>
  <label style="margin-top:.5rem;font-weight:400">Manual trends
    <span style="font-weight:400;color:#888">(comma-separated, feeds “Manual”)</span>
   <input name="trend_terms" placeholder="teeth whitening strips, stanley cup" value="{trend_terms}"></label>
 </div>
 <label class="wide" style="flex-direction:row;align-items:center;gap:.5rem;font-weight:600">
  <input type="checkbox" name="mismatch" value="1" {mismatch_chk} style="width:auto">
  🎯 Or Mismatch scan — sweep a whole Argos category / Sale and let the price gap
  surface winners (buy Argos → resale Amazon primary, eBay secondary)</label>
 <div class="wide" style="background:rgba(127,127,127,.06);border-radius:8px;padding:.6rem .8rem">
  <div style="display:flex;flex-wrap:wrap;gap:.6rem 1rem;align-items:flex-end">
   <label style="font-weight:400">Category to sweep
    <select name="category">{category_options}</select></label>
   <label style="font-weight:400">Pages <span style="color:#888">(~60 items/page)</span>
    <input name="pages" type="number" min="1" max="10" value="{pages}" style="width:5rem"></label>
   <label style="font-weight:400;flex:1;min-width:13rem">…or paste an Argos category/Sale URL or term
    <input name="category_url" placeholder="https://www.argos.co.uk/list/sale" value="{category_url}"></label>
  </div>
  <p class="note" style="margin:.4rem 0 0">eBay's free API prices the whole sweep, then only the
   profitable survivors are confirmed on Amazon — the better flip leads each row, the other venue is
   shown alongside. Prices are active-listing asks, not sold data.</p>
 </div>
 <label class="local-only">Fetch via
  <select name="fetch_mode">
   <option value="chrome" {fm_chrome}>My Chrome (autonomous — recommended)</option>
   <option value="browser" {fm_browser}>Fresh browser (often blocked by Akamai)</option>
  </select></label>
 <label class="local-only">My Chrome debug URL <span style="font-weight:400">(for “My Chrome”)</span>
  <input name="cdp_url" value="{cdp_url}"></label>
 <p class="note local-only">“My Chrome” launches a real Chrome for you automatically when you
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
 <label>Min net £ <span style="font-weight:400">(profit gate)</span>
  <input name="min_net" type="number" step="0.01" value="{min_net}"></label>
 <label>Min ROI % <span style="font-weight:400">(return on capital)</span>
  <input name="min_roi" type="number" step="1" value="{min_roi}"></label>
 <label>Min match % <span style="font-weight:400">(reject weak matches)</span>
  <input name="min_match" type="number" step="1" value="{min_match}"></label>
 <label class="wide" style="flex-direction:row;align-items:center;gap:.5rem;font-weight:400">
  <input type="checkbox" name="show_all" value="1" {show_all_chk} style="width:auto">
  Show all results <span style="color:#888">— ignore the profit gates for this scan
  (inspect the full market, including losers)</span></label>
 <label class="local-only">eBay Client ID <span style="font-weight:400">(App ID — only for eBay)</span>
  <input name="ebay_id" autocomplete="off" value="{ebay_id}"></label>
 <label class="local-only">eBay Client Secret <span style="font-weight:400">(Cert ID — stays on this machine)</span>
  <input name="ebay_secret" type="password" autocomplete="off" placeholder="{secret_ph}"></label>
 <label class="wide local-only" style="flex-direction:row;align-items:center;gap:.5rem;font-weight:400">
  <input type="checkbox" name="remember" value="1" {remember_chk} style="width:auto">
  Remember these on this machine (saved unencrypted to {config_path})</label>
 <p class="note">Runs locally on 127.0.0.1. Credentials are sent only to eBay's API.
    {saved_note}</p>
 <button type="submit">Run scan</button>
</form>
{results}
<p class="note local-only" style="text-align:center;margin-top:2.5rem;border-top:1px solid #8883;padding-top:1rem">
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


def _discovery_summary(targets) -> str:
    items = []
    for t in targets:
        seas = f", seasonal {t.seasonal_strength:.2f}" if t.seasonal_strength else ""
        agree = ", ".join(html.escape(s) for s in t.sources)
        leads = (f' <span style="color:#888">— leads: {html.escape(", ".join(t.leads))}</span>'
                 if t.leads else "")
        items.append(
            f'<li><b>{html.escape(t.term)}</b> '
            f'<span style="color:#888">(score {t.discovery_score:.2f}; {agree}{seas})</span>'
            f'{leads}</li>')
    return (f'<p class="note">Discovered <b>{len(targets)}</b> categories '
            '(discovery priority — more sources agreeing ranks higher):</p>'
            f'<ul style="font-size:.85rem;line-height:1.6">{"".join(items)}</ul>')


def _opportunities_table(opps) -> str:
    # Multi-venue (mismatch scan) mode: rows may resell on different venues, and
    # each carries a secondary venue's price. Then we show a per-row "Sell on"
    # column + the 2nd venue's price. A single-venue hunt keeps the classic layout.
    multi = any(getattr(o, "secondary_market", None) for o in opps) or \
        len({o.marketplace_match.market for o in opps}) > 1
    market = getattr(opps[0].marketplace_match, "market", "resale")

    if multi:
        head = ("<tr><th>Product</th><th>Source</th><th>Buy £</th><th>Sell on</th>"
                "<th>Resale £</th><th>2nd venue £</th><th>Net £</th><th>ROI</th>"
                "<th>Score</th><th>Dem</th><th>Match</th></tr>")
    else:
        head = (f"<tr><th>Product</th><th>Source</th><th>Buy £</th>"
                f"<th>Est. {html.escape(market)} £</th>"
                "<th>Net £</th><th>ROI</th><th>Score</th><th>Dem</th><th>Match</th><th>T/S</th></tr>")
    rows = []
    for o in opps:
        c, p = o.marketplace_match, o.retailer_product
        plink = (f'<a href="{html.escape(p.url)}" target="_blank" rel="noopener">'
                 f'{html.escape(p.name)}</a>')
        murl = getattr(c, "market_url", "")
        mcell = (f'<a href="{html.escape(murl)}" target="_blank" rel="noopener">'
                 f'{o.expected_sale_price:.2f}</a>' if murl else f"{o.expected_sale_price:.2f}")
        src = SOURCES[p.source].label if p.source in SOURCES else p.source
        if multi:
            sec = getattr(o, "secondary_market", None)
            surl = getattr(o, "secondary_url", None) or ""
            if o.secondary_price is not None:
                s2 = f"{o.secondary_price:.2f}"
                s2cell = (f'{html.escape(sec)} <a href="{html.escape(surl)}" target="_blank" '
                          f'rel="noopener">{s2}</a>' if surl else f"{html.escape(sec)} {s2}")
            else:
                s2cell = "—"
            rows.append(
                f"<tr><td>{plink}</td><td>{html.escape(src)}</td><td>{o.buy_price:.2f}</td>"
                f"<td>{html.escape(c.market)}</td><td>{mcell}</td><td>{s2cell}</td>"
                f"{_fmt_signed(o.estimated_net_profit)}"
                f"<td>{o.roi * 100:.0f}%</td><td>{o.opportunity_score:.2f}</td>"
                f"<td>{o.demand_confidence:.2f}</td><td>{o.match_confidence:.2f}</td></tr>")
        else:
            ts = max(o.trend_strength, o.seasonal_strength)
            rows.append(
                f"<tr><td>{plink}</td><td>{html.escape(src)}</td><td>{o.buy_price:.2f}</td>"
                f"<td>{mcell}</td>{_fmt_signed(o.estimated_net_profit)}"
                f"<td>{o.roi * 100:.0f}%</td><td>{o.opportunity_score:.2f}</td>"
                f"<td>{o.demand_confidence:.2f}</td><td>{o.match_confidence:.2f}</td>"
                f"<td>{ts:.2f}</td></tr>")

    if multi:
        note = ('<b>Buy at Argos → resell online. "Resale £" is the chosen venue\'s median '
                'of ACTIVE listings (Amazon preferred, else eBay); "2nd venue £" is the other '
                'venue for comparison — both are asking prices, NOT completed/sold data.</b> '
                'Net £ and ROI derive from the chosen venue. Verify realistic sale price, fees, '
                'postage and stock before buying. Sorted profit-first: net → ROI → demand → '
                f'match. {len(opps)} opportunity(ies).')
    else:
        note = (f'<b>“Est. {html.escape(market)} £” is the median of ACTIVE '
                f'{html.escape(market)} listings — asking prices, not completed/sold data.</b> It '
                'shows the potential spread, not a confirmed sale price; Net £ and ROI are derived '
                'from it. Verify sold volume, realistic sale price, postage and stock before buying. '
                'Sorted profit-first: net → ROI → demand → match → trend/seasonal. Dem/Match/T-S are '
                f'0–1 confidences. {len(opps)} opportunity(ies).')
    return f"<table>{head}{''.join(rows)}</table><p class=\"note\">{note}</p>"


def _run_hunt(client, comparator, source, fetch_mode, cdp_url, fees, postage,
              signals, trend_terms, limit, min_net, min_roi, min_match,
              progress=None) -> str:
    """Demand-guided discovery → retail search → profit-first opportunities."""
    from .browser import BrowserFetcher
    from .chrome_launch import ensure_chrome
    from .http import PoliteSession
    from .opportunity import (build_opportunity, rank_opportunities,
                              write_opportunities_csv)
    from .trends.engine import TrendEngine, build_providers

    progress = progress or (lambda *a, **k: None)
    needs_browser = any(s in ("movers", "bestsellers") for s in signals)
    session = PoliteSession()
    if fetch_mode == "chrome":
        ok, msg = ensure_chrome(cdp_url, open_url="https://www.amazon.co.uk/gp/movers-and-shakers")
        if not ok and needs_browser:
            return f'<p class="err">{html.escape(msg)}</p>'
        fetcher = BrowserFetcher(cdp_url=cdp_url)
        sc_mode = "browser"
    else:
        fetcher = BrowserFetcher(headless=False) if needs_browser else None
        sc_mode = "auto"

    providers = build_providers(signals, manual_terms=trend_terms, amazon_limit=limit)
    engine = TrendEngine(providers)
    targets, opportunities, seen = [], [], set()
    try:
        progress("discovering", f"reading signals: {', '.join(signals)}")
        targets = engine.discover(session=session, browser=fetcher, limit=limit)
        if targets:
            scraper = make_scraper(source, session, fetcher, fetch_mode=sc_mode)
            for i, t in enumerate(targets, 1):
                progress("searching_retailer",
                         f"{SOURCES[source].label}: {t.term} ({i}/{len(targets)})")
                try:
                    prods = scraper.scrape(t.term, max_products=3)
                except ScrapeBlocked:
                    continue
                prods = [p for p in prods if p.url not in seen]
                seen.update(p.url for p in prods)
                if not prods:
                    continue
                progress("collecting_prices", f"pricing {len(prods)} × {t.term}")
                for c in compare_products(prods, client):
                    o = build_opportunity(c, fees, postage, t.trend_strength, t.seasonal_strength)
                    if o is not None:
                        opportunities.append(o)
        progress("calculating", "ranking opportunities by net profit & ROI")
    finally:
        if fetcher is not None:
            try:
                fetcher.close()
            except Exception:  # noqa: BLE001
                pass

    if not targets:
        return ('<p class="err">No candidate categories from the chosen signals. For the '
                'Amazon signals, make sure “My Chrome” is running and you\'ve visited '
                'amazon.co.uk once in it (solve any robot check). Or tick “Manual” and add a '
                'trend term.</p>')
    if comparator == "amazon" and getattr(client, "blocked", False) and not opportunities:
        return ('<p class="err">Amazon (the price check) showed a robot check — solve it in '
                'the open Amazon tab, then hunt again.</p>')
    ranked = rank_opportunities(opportunities, min_net=min_net, min_roi=min_roi, min_match=min_match)
    summary = _discovery_summary(targets)
    if not ranked:
        if not opportunities:  # nothing was priced at all — genuinely empty
            return (summary + f'<p class="err">Discovered {len(targets)} categories but no '
                    'products could be priced (no credible resale matches). Add more signals, '
                    'or check My Chrome cleared the retailer bot-check.</p>')
        # Priced products exist but none cleared the gates — never dead-end.
        # Show the closest near-misses so you can judge and adjust, clearly flagged.
        fallback = rank_opportunities(opportunities)[:limit]
        write_opportunities_csv(fallback, RESULTS_PATH)
        gates = _thresholds_label(min_net, min_roi, min_match)
        banner = (f'<p class="err">None of the {len(opportunities)} priced product(s) cleared '
                  f'your {gates} — showing the {len(fallback)} closest below-threshold, most '
                  'profitable first, so you can judge. Lower the gates or tick "Show all '
                  'results" to keep results like these.</p>')
        return (summary + banner + _opportunities_table(fallback)
                + '<p class="note"><a href="/download">⬇ Download results.csv</a></p>')
    write_opportunities_csv(ranked, RESULTS_PATH)
    return (summary + _opportunities_table(ranked)
            + '<p class="note"><a href="/download">⬇ Download results.csv</a></p>')


def _run_mismatch(form, fetch_mode, cdp_url, fees, postage, max_products,
                  min_net, min_roi, min_match, progress=None) -> str:
    """Wide-net mismatch scan through the two-stage funnel: sweep an Argos
    category, price the whole pool on eBay's free API (cheap wide filter), then
    confirm only the survivors on Amazon (the expensive browser step). The
    higher-net venue leads each row; the other is shown alongside."""
    from .browser import BrowserFetcher
    from .funnel import find_opportunities
    from .http import PoliteSession
    from .mismatch import ARGOS_CATEGORIES, sweep_products
    from .opportunity import rank_opportunities, write_opportunities_csv
    from .providers import amazon_provider, ebay_provider
    from .sources.base import resolve_target

    progress = progress or (lambda *a, **k: None)
    source = "argos"
    # A pasted URL/term wins over the category dropdown (sidesteps stale presets).
    target = (form.get("category_url", "").strip()
              or form.get("category", "").strip() or "sale")
    try:
        pages = max(1, min(int(form.get("pages") or 3), 10))
    except ValueError:
        pages = 3
    if HOSTED:  # server can't reach your Mac's Chrome — use in-container Chromium
        fetch_mode = "browser"

    # Cheap wide filter = eBay Browse API (free, no browser) when creds exist;
    # expensive enrich = Amazon via real Chrome. Without eBay creds we fall back
    # to Amazon-only (no cheap stage) — slower, so keep the pool small.
    saved = config.load_credentials()
    cid = form.get("ebay_id", "").strip() or saved["ebay_client_id"]
    secret = form.get("ebay_secret", "").strip() or saved["ebay_client_secret"]
    amazon = amazon_provider(cdp_url if fetch_mode == "chrome" else None)
    if cid and secret:
        if form.get("remember"):
            config.save_credentials(cid, secret)
        env = "SANDBOX" if form.get("ebay_env") == "SANDBOX" else "PRODUCTION"
        cheap, rich = ebay_provider(cid, secret, env=env), amazon
        venues = "eBay API (wide) → Amazon (confirm winners)"
    else:
        cheap, rich = amazon, None  # Amazon-only, no cheap pre-filter
        venues = "Amazon only (add an eBay key to filter cheaply first)"

    open_url = ARGOS_CATEGORIES[target][1] if target in ARGOS_CATEGORIES \
        else resolve_target(source, target)
    label = ARGOS_CATEGORIES[target][0] if target in ARGOS_CATEGORIES else target

    fetcher = None
    try:
        if fetch_mode == "chrome":
            from .chrome_launch import ensure_chrome
            ok, chrome_msg = ensure_chrome(cdp_url, open_url=open_url)
            if not ok:
                return f'<p class="err">{html.escape(chrome_msg)}</p>'
            fetcher = BrowserFetcher(cdp_url=cdp_url)
            scraper = make_scraper(source, PoliteSession(), fetcher, fetch_mode="browser")
        else:
            fetcher = BrowserFetcher(headless=False)
            scraper = make_scraper(source, PoliteSession(), fetcher)

        progress("searching_retailer", f"sweeping Argos {label} ({pages} page(s))")
        products = sweep_products(scraper, target, pages=pages, max_products=max_products)
        if not products:
            return ('<p class="err">Swept the Argos pool but found no products — the category '
                    'link may have changed. Paste a live Argos category/Sale URL in the field '
                    'and try again, or check My Chrome cleared the bot-check.</p>')
        result = find_opportunities(
            products, cheap, rich, fees=fees, postage=postage,
            min_net=min_net, min_roi=min_roi, min_match=min_match,
            enrich_cap=(max_products or 20), progress=progress)
    except ScrapeBlocked as exc:
        return f'<p class="err">{html.escape(str(exc).splitlines()[-1])}</p>'
    finally:
        for c in (fetcher, amazon):
            try:
                c.close()
            except Exception:  # noqa: BLE001 - teardown is best-effort
                pass

    progress("calculating", "ranking opportunities by net profit & ROI")
    summary = (f'<p class="note">Swept <b>{len(products)}</b> products from Argos '
               f'<b>{html.escape(str(label))}</b> · priced via {html.escape(venues)}.</p>')
    if not result.opportunities:
        if not result.staged:
            return (summary + '<p class="err">None of the swept products had a credible resale '
                    'match. Try a different category, or more pages.</p>')
        # Nothing cleared the gates — show the closest cheap-stage near-misses
        # (already priced for free, so no extra cost) rather than dead-end.
        fallback = result.staged[: (max_products or 20)]
        write_opportunities_csv(fallback, RESULTS_PATH)
        gates = _thresholds_label(min_net, min_roi, min_match)
        banner = (f'<p class="err">None of the {result.n_priced} priced product(s) cleared '
                  f'your {gates} — showing the {len(fallback)} closest below-threshold, most '
                  'profitable first. Lower the gates or tick "Show all results".</p>')
        return (summary + banner + _opportunities_table(fallback)
                + '<p class="note"><a href="/download">⬇ Download results.csv</a></p>')
    write_opportunities_csv(result.opportunities, RESULTS_PATH)
    return (summary + _opportunities_table(result.opportunities)
            + '<p class="note"><a href="/download">⬇ Download results.csv</a></p>')


def _run_scan(form, files=None, progress=None) -> str:
    files = files or {}
    progress = progress or (lambda *a, **k: None)
    query = form.get("url", "").strip()
    comparator = form.get("comparator", "ebay")
    source = form.get("source") if form.get("source") in SOURCES else "argos"
    fetch_mode = form.get("fetch_mode") or "chrome"
    cdp_url = form.get("cdp_url", "").strip() or "http://127.0.0.1:9222"
    if HOSTED:  # server can't reach your Mac's Chrome — use in-container Chromium
        fetch_mode = "browser"
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
    min_roi = _opt_pct(form.get("min_roi"))       # "15" -> 0.15
    min_match = _opt_pct(form.get("min_match"))    # "80" -> 0.80
    if form.get("show_all"):  # escape hatch: drop the profit gates for this scan
        min_net = min_roi = min_match = None

    if form.get("mismatch"):  # wide-net sweep: buy Argos, resale Amazon+eBay
        return _run_mismatch(form, fetch_mode, cdp_url, fees, postage,
                             max_products, min_net, min_roi, min_match, progress)

    signals = [name for name, field in SIGNAL_FIELDS.items() if form.get(field)] or DEFAULT_SIGNALS
    trend_terms = [t.strip() for t in (form.get("trend_terms", "") or "")
                   .replace("\n", ",").split(",") if t.strip()]

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
            return _run_hunt(client, comparator, source, fetch_mode, cdp_url,
                             fees, postage, signals, trend_terms, max_products,
                             min_net, min_roi, min_match, progress)
        if upload_html:
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


def _category_options(form) -> str:
    from .mismatch import ARGOS_CATEGORIES
    selected = form.get("category") or "sale"
    opts = []
    for key, (label, _url) in ARGOS_CATEGORIES.items():
        sel = "selected" if key == selected else ""
        opts.append(f'<option value="{html.escape(key)}" {sel}>{html.escape(label)}</option>')
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
    agent_status = ""
    if HOSTED:
        seen = store().agent_last_seen
        if seen is not None and (time.time() - seen) < 30:
            agent_status = ('<p class="note">🟢 Mac agent connected — scans run on your '
                            'Mac. Keep it awake with the agent running.</p>')
        else:
            when = f"last seen {int(time.time() - seen)}s ago" if seen else "not seen yet"
            agent_status = ('<p class="err">🔴 No Mac agent connected (' + when + '). Run '
                            '<code>scripts/agent.command</code> on your Mac — scans queue '
                            "until it's up.</p>")
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
        min_net=_prefill(form, "min_net", DEFAULT_MIN_NET),
        min_roi=_prefill(form, "min_roi", DEFAULT_MIN_ROI),
        min_match=_prefill(form, "min_match", DEFAULT_MIN_MATCH),
        show_all_chk="checked" if form.get("show_all") else "",
        cdp_url=html.escape(cdp_url),
        chrome_status=chrome_status,
        hunt_chk="checked" if form.get("hunt") else "",
        trend_terms=html.escape(form.get("trend_terms", "")),
        mismatch_chk="checked" if form.get("mismatch") else "",
        category_options=_category_options(form),
        pages=html.escape(str(form.get("pages", "3"))),
        category_url=html.escape(form.get("category_url", "")),
        sig_movers=_sig_chk(form, "movers", True),
        sig_bestsellers=_sig_chk(form, "bestsellers", False),
        sig_seasonal=_sig_chk(form, "seasonal", True),
        sig_manual=_sig_chk(form, "manual", True),
        fm_chrome="selected" if fetch_mode != "browser" else "",
        fm_browser="selected" if fetch_mode == "browser" else "",
        # Pre-fill the Client ID from saved/env; never pre-fill the secret field.
        ebay_id=html.escape(form.get("ebay_id") or saved["ebay_client_id"]),
        secret_ph="saved secret will be used — leave blank" if have_secret else "",
        remember_chk="checked" if (form.get("remember") or config.has_saved_secret()) else "",
        config_path=html.escape(str(config.CONFIG_PATH)),
        saved_note=saved_note,
        version=html.escape(_version()),
        agent_status=agent_status,
        local_only_css=(".local-only{display:none !important}" if HOSTED else ""),
        g_sel="selected" if comparator == "google" else "",
        e_sel="selected" if comparator == "ebay" else "",
        a_sel="selected" if comparator == "amazon" else "",
        prd_sel="selected" if env != "SANDBOX" else "",
        sbx_sel="selected" if env == "SANDBOX" else "",
        results=results,
    )


def _progress_html(job_id: str, job: dict) -> str:
    from .jobs import STAGES
    dots = "".join(f'<li id="st-{s}">{s.replace("_", " ")}</li>' for s in STAGES)
    detail = html.escape(job.get("detail") or "starting…")
    return f"""
<div class="err" style="background:rgba(127,127,127,.12)">
 <p style="margin:.2rem 0"><b>Scan running…</b> <span id="detail">{detail}</span></p>
 <ol id="pipeline">{dots}</ol>
 <p class="note">This updates itself. You can close it and reopen on another
    device — the result is saved.</p>
</div>
<style>
 #pipeline li{{opacity:.4;margin:.15rem 0}}
 #pipeline li.active{{opacity:1;font-weight:700}}
 #pipeline li.done{{opacity:.65;text-decoration:line-through}}
</style>
<script>
const STAGES={json.dumps(STAGES)};
async function poll(){{
  try{{
    const j=await (await fetch("/jobs/{job_id}/status")).json();
    document.getElementById("detail").textContent=j.detail||j.status;
    let seen=false;
    for(const s of STAGES){{
      const el=document.getElementById("st-"+s); if(!el)continue;
      if(s===j.stage){{el.className="active";seen=true;}}
      else el.className=seen?"":"done";
    }}
    if(j.status==="done"){{location.reload();return;}}
    if(j.status==="error"){{document.getElementById("detail").textContent="Error: "+j.error;return;}}
  }}catch(e){{}}
  setTimeout(poll,2000);
}}
poll();
</script>"""


def _form_from(job: dict):
    try:
        return json.loads(job.get("params") or "{}")
    except (ValueError, TypeError):
        return {}


@app.route("/")
def index() -> str:
    latest = store().latest(status="done")
    results = ""
    if latest and latest.get("result_html"):
        results = (f'<p class="note">Showing your latest run · '
                   f'<a href="/jobs/{latest["id"]}">permalink</a></p>'
                   + latest["result_html"])
    return _render(results=results)


@app.route("/scan", methods=["POST"])
def scan():
    params = {k: request.form.get(k) for k in request.form.keys()}
    up = request.files.get("page")
    if up is not None and getattr(up, "filename", ""):
        try:
            params["_upload_html"] = up.read().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass
    job_id = store().enqueue(params)
    return redirect(f"/jobs/{job_id}")


@app.route("/jobs/<job_id>")
def job_view(job_id):
    job = store().get(job_id)
    if not job:
        return _render(results='<p class="err">No such scan.</p>')
    form = _form_from(job)
    if job["status"] == "done":
        return _render(results=(job["result_html"] or ""), form=form)
    if job["status"] == "error":
        return _render(results=f'<p class="err">Scan failed: {html.escape(job["error"] or "")}</p>',
                       form=form)
    return _render(results=_progress_html(job_id, job), form=form)


@app.route("/jobs/<job_id>/status")
def job_status(job_id):
    from flask import jsonify
    job = store().get(job_id)
    if not job:
        return jsonify({"error": "no such job"}), 404
    return jsonify({"status": job["status"], "stage": job["stage"],
                    "detail": job["detail"] or "", "error": job["error"] or ""})


def _login_page(error: str = "") -> str:
    err = f'<p style="background:#c443;padding:.6rem;border-radius:6px">{html.escape(error)}</p>' if error else ""
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sign in — retail-arbitrage-finder</title>
<style>:root{{color-scheme:light dark}} body{{font-family:system-ui,sans-serif;
max-width:340px;margin:15vh auto;padding:0 1rem}} input,button{{width:100%;
font-size:1rem;padding:.6rem;margin-top:.6rem;box-sizing:border-box;border-radius:6px;
border:1px solid #8888}} button{{background:#2d7;color:#062;font-weight:700;border:0}}
</style></head><body><h2>retail-arbitrage-finder</h2>{err}
<form method="post" action="/login">
 <input type="password" name="password" placeholder="Password" autofocus>
 <button type="submit">Sign in</button></form></body></html>"""


@app.route("/login", methods=["GET", "POST"])
def login():
    if not _auth_on():
        return redirect("/")
    if request.method == "POST":
        given = request.form.get("password", "")
        if hmac.compare_digest(given, os.environ.get("ARBFINDER_PASSWORD", "")):
            session["authed"] = True
            return redirect("/")
        return _login_page("Wrong password — try again.")
    return _login_page()


@app.route("/logout")
def logout():
    session.clear()
    return redirect("/login")


@app.route("/chrome")
def chrome():
    from .chrome_launch import ensure_chrome
    ok, msg = ensure_chrome("http://127.0.0.1:9222")
    cls = "note" if ok else "err"
    return _render(results=f'<p class="{cls}">{html.escape(msg)}</p>')


@app.route("/download")
def download():
    from flask import send_file
    if RESULTS_PATH.exists():
        return send_file(RESULTS_PATH, as_attachment=True, download_name="opportunities.csv")
    return _render(results='<p class="err">No results yet — run a hunt first.</p>')


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
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    store()  # start the background scan worker (Playwright runs on that thread)

    if HOSTED:  # public bind, fixed port, no browser auto-open
        port = int(os.environ.get("PORT") or os.environ.get("ARBFINDER_PORT", "8080"))
        gate = "password-gated" if _auth_on() else "OPEN (set ARBFINDER_PASSWORD!)"
        print(f"retail-arbitrage-finder (hosted, {gate}) → 0.0.0.0:{port}")
        app.run(host="0.0.0.0", port=port, threaded=True)  # threaded: scans run off-request
        return

    requested = int(os.environ.get("ARBFINDER_PORT", "5000"))
    port = _free_port(requested)
    os.environ["ARBFINDER_PORT"] = str(port)  # pin so "Update & restart" keeps the URL
    url = f"http://127.0.0.1:{port}"
    if port != requested:
        print(f"Port {requested} was busy (often macOS AirPlay Receiver, which makes the "
              f"browser DOWNLOAD the page instead of showing it) — using {port} instead.")
    print(f"retail-arbitrage-finder dashboard → {url}  (Ctrl+C to stop)")
    if os.environ.get("ARBFINDER_NO_BROWSER") != "1":
        import threading
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    # threaded: the scan runs on the worker thread, so requests stay quick.
    app.run(host="127.0.0.1", port=port, threaded=True)


if __name__ == "__main__":
    main()
