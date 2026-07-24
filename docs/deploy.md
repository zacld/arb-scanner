# Deploying the dashboard as a hosted web app (Fly.io)

Turns the local dashboard into a **password-gated public HTTPS URL** you can open
from your phone or any browser — no Mac process required. The scan runs on the
server as a background job with a live progress pipeline, and results are saved
(SQLite on a persistent disk) so the latest run shows on any device after a
refresh.

## What works hosted vs local

- **eBay comparator** (Browse API) and **seasonal / manual discovery** run fully
  on the server — no browser, always reliable.
- **Retailer scraping (Argos / Amazon)** is **best-effort from a data-centre IP**
  — Akamai/Amazon block cloud browsers far more than your home Chrome, so expect
  the pipeline to show a `searching_retailer` error sometimes. For reliable
  retailer scraping either run **local mode** on your Mac (`--via chrome`) or add
  a paid unlocker later (the fetch layer is pluggable — see "Later").

## One-time setup

1. Install the Fly CLI and sign in:
   ```bash
   brew install flyctl && fly auth login
   ```
2. From the repo root, create the app (this also picks a unique name and writes
   it into `fly.toml` — or edit `app = "..."` yourself first):
   ```bash
   fly launch --no-deploy --copy-config
   ```
3. Create the persistent volume for the jobs DB + results:
   ```bash
   fly volumes create arbfinder_data --size 1 --region lhr
   ```
4. Set secrets (never commit these):
   ```bash
   fly secrets set \
     ARBFINDER_PASSWORD='a-strong-password' \
     ARBFINDER_SECRET_KEY="$(python -c 'import secrets;print(secrets.token_hex(32))')" \
     EBAY_CLIENT_ID='your-ebay-app-id' \
     EBAY_CLIENT_SECRET='your-ebay-cert-id'
   ```
   - `ARBFINDER_PASSWORD` turns on the login gate (without it the site is open —
     always set it).
   - `ARBFINDER_SECRET_KEY` signs the login cookie (stable so logins survive
     restarts).
   - eBay creds are read server-side only; they are never rendered in the page.

## Deploy

```bash
fly deploy
```

Then `fly open` (or the URL Fly prints). On your phone: open the URL → sign in →
tick **🔥 hunt trending** (or type a search term) → **Run scan** → watch the
stage pipeline → results persist and reappear on refresh or another device.

## How it runs

- `Dockerfile` uses Playwright's official Python image (Chromium + deps
  bundled). `gunicorn` serves with **1 worker** (so the job store + scan worker
  thread are single-instance) and threads for concurrent status polls.
- `ARBFINDER_HOSTED=1` (set in `fly.toml`) binds public, requires login, hides the
  local-only controls (My-Chrome debug URL, eBay secret field, Update button),
  and forces the in-container Chromium.
- SQLite jobs DB + `results.csv` live on `/data` (the mounted volume), so they
  survive deploys/restarts.

## Local mode is unchanged

`python -m arbfinder.dashboard` (or `scripts/start.command`) still runs on
`127.0.0.1`, opens your browser, and offers **My Chrome** (`--via chrome`) — the
reliable scraping path when you're at your Mac.

## Later: reliable cloud retailer scraping

The retailer fetch is designed to be swapped for a bot-bypass/residential service
(ScraperAPI, Zyte, Bright Data Web Unlocker, Browserless-residential) without
touching scan logic — see `docs/sold-price-layer.md`-style provider seam noted in
the plan. That's the paid upgrade that makes Argos/Amazon scraping reliable from
the cloud.
