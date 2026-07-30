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

## One-time setup (do these in order)

1. **Install the Fly CLI and sign in:**
   ```bash
   brew install flyctl && fly auth login
   ```
2. **Create the app** (Fly generates a unique name and writes it into `fly.toml`):
   ```bash
   fly launch --no-deploy --copy-config
   ```
   Note the name it prints (e.g. `arb-scanner-solitary-snow-8410`). Confirm
   `fly.toml`'s `app =` line matches it — if not, set it:
   ```bash
   sed -i '' 's/^app = .*/app = "YOUR-APP-NAME-HERE"/' fly.toml && grep '^app' fly.toml
   ```
3. **Run ONE machine** (this app is single-instance — SQLite + worker):
   ```bash
   fly scale count 1
   ```
4. **Create the persistent volume** (jobs DB + results):
   ```bash
   fly volumes create arbfinder_data --size 1 --region lhr
   ```
5. **Set ALL secrets at once** — one line each, no stray spaces after `\`:
   ```bash
   fly secrets set \
     ARBFINDER_PASSWORD='a-strong-password' \
     ARBFINDER_SECRET_KEY="$(python3 -c 'import secrets;print(secrets.token_hex(32))')" \
     ARBFINDER_AGENT_TOKEN="$(python3 -c 'import secrets;print(secrets.token_hex(24))')" \
     EBAY_CLIENT_ID='your-ebay-app-id' \
     EBAY_CLIENT_SECRET='your-ebay-cert-id'
   ```
   - `ARBFINDER_PASSWORD` — the login gate (without it the site is open; always set it).
   - `ARBFINDER_SECRET_KEY` — signs the login cookie (stable across restarts).
   - `ARBFINDER_AGENT_TOKEN` — lets your Mac agent connect (see below). **Save this
     value** — you'll paste the same one into `~/.arbfinder-agent.env`. Read it
     back any time with `fly ssh console -C 'printenv ARBFINDER_AGENT_TOKEN'`.
   - eBay creds are read server-side only; never rendered in the page.

## Deploy

```bash
fly deploy
```

If it errors `needs volumes … lhr=1`, you have >1 machine — run `fly scale count 1`
and deploy again. When it finishes, `fly apps open` (or the URL Fly prints). You
should get the **login page**.

**On the hosted site alone, live retail scraping is blocked** (data-centre IP), so
a hunt shows "0 priced products". The fix is the **Mac agent** below — that's what
makes scan-from-phone actually work.

## How it runs

- `Dockerfile` uses Playwright's official Python image; `gunicorn` serves with
  **1 worker** + threads (single-instance job store; concurrent status polls).
- `ARBFINDER_HOSTED=1` (set in `fly.toml`) binds public, requires login, hides the
  local-only controls (My-Chrome debug URL, eBay secret field, Update button), and
  puts the app in **broker mode**: it does NOT run scans itself — queued jobs wait
  for the Mac agent to claim and run them.
- SQLite jobs DB + `results.csv` live on `/data` (the mounted volume), so they
  survive deploys/restarts.

## Local mode is unchanged

`python -m arbfinder.dashboard` (or `scripts/start.command`) still runs on
`127.0.0.1`, opens your browser, and offers **My Chrome** (`--via chrome`) — the
reliable scraping path when you're at your Mac.

## Reliable retail scraping: the Mac-agent hybrid (recommended, free)

Argos/Amazon block the data-centre IP, so live retail scraping fails from the
cloud. The free fix is the **Mac agent**: your phone triggers scans on the hosted
site, and a small worker on your Mac (residential IP + real Chrome) runs them and
posts results back. You already set `ARBFINDER_AGENT_TOKEN` in step 5 above — now
on your Mac:

```bash
cat > ~/.arbfinder-agent.env <<EOF
export ARBFINDER_AGENT_URL='https://<your-app>.fly.dev'
export ARBFINDER_AGENT_TOKEN='<the same token from step 5>'
EOF
```

Then double-click **`scripts/agent.command`** (or `python -m arbfinder.agent`).
Full details in **`docs/agent.md`**. In hosted mode the server no longer scrapes
itself — jobs wait for the agent, and the page shows a 🟢/🔴 agent-connected
banner.

Alternatively, a paid unlocker (ScraperAPI, Zyte, Bright Data) can do the cloud
scraping with no Mac — the fetch layer is built to accept it as a drop-in.
