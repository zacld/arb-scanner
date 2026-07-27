# Mac-agent hybrid: scan from your phone, scraped on your Mac

The hosted site can do everything except **scrape Argos/Amazon** — a data-centre
IP is blocked by their bot protection. Your Mac isn't (residential IP + real
Chrome). So run a small **agent** on your Mac: it claims the scans your phone
queues on the hosted site, runs them locally, and posts the results back — which
then show up in the cloud on any device.

    phone → hosted site → job queued
    Mac agent → claims it → scrapes on your Mac → posts results back
    phone → sees the results (saved in the cloud, viewable anywhere)

Trade-off: your Mac must be **awake with the agent running** when a scan fires.
If it's asleep, the job waits in the queue until the Mac is back.

## One-time setup

1. **Pick a shared token** and set it as a Fly secret on the site:
   ```bash
   fly secrets set ARBFINDER_AGENT_TOKEN="$(python3 -c 'import secrets;print(secrets.token_hex(24))')"
   ```
   Copy the value you set (see it with `fly secrets list` shows only digests, so
   generate it into a variable you can read, e.g. run the python one-liner first
   and paste the same string into both the secret and the file below).

2. **Save the URL + token on your Mac** in `~/.arbfinder-agent.env`:
   ```bash
   cat > ~/.arbfinder-agent.env <<'EOF'
   export ARBFINDER_AGENT_URL='https://<your-app>.fly.dev'
   export ARBFINDER_AGENT_TOKEN='<the same token you set as the Fly secret>'
   EOF
   ```

3. Make sure your **eBay creds** are in your shell (they already are, in
   `~/.zshrc`): `EBAY_CLIENT_ID` / `EBAY_CLIENT_SECRET`. The scan runs on the Mac,
   so it uses these — the cloud needs no eBay secret for scanning.

## Run it

Double-click **`scripts/agent.command`** (or run it). It pulls the latest code,
activates the venv, and starts polling. Leave the window open.

Now open the hosted site on your phone — the banner shows **🟢 Mac agent
connected** — type a scan or hunt, hit Run, and watch the pipeline. The scrape
happens on your Mac; the result lands back in the site.

Manual equivalent:
```bash
python -m arbfinder.agent --url https://<your-app>.fly.dev --token <TOKEN>
```

## Notes

- The site shows **🔴 No Mac agent connected** when nothing has polled in ~30s —
  that's your cue the Mac agent isn't running.
- A job claimed by an agent that then dies/sleeps is automatically re-queued after
  10 minutes, so it isn't stuck forever.
- The `/agent/*` endpoints are gated by `ARBFINDER_AGENT_TOKEN` (constant-time
  compare) — separate from your login password.
- Local mode is unchanged: `python -m arbfinder.dashboard` on your Mac still runs
  scans in-process with no agent needed.
