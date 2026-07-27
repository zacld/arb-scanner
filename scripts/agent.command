#!/bin/bash
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  retail-arbitrage-finder — Mac agent                                     │
# │                                                                          │
# │  Runs the scans your hosted site queues, here on your Mac (real Chrome + │
# │  home IP, so Argos/Amazon don't block it). Leave it running while you    │
# │  want to scan from your phone. Double-click to start.                    │
# │                                                                          │
# │  First time: create ~/.arbfinder-agent.env with two lines —              │
# │    export ARBFINDER_AGENT_URL='https://<your-app>.fly.dev'               │
# │    export ARBFINDER_AGENT_TOKEN='<the token you set as a Fly secret>'    │
# └─────────────────────────────────────────────────────────────────────────┘

cd "$(dirname "$0")/.." || exit 1
BRANCH="claude/retail-arbitrage-finder-tjc9k2"

# Load the saved URL + token (kept out of the repo).
[ -f "$HOME/.arbfinder-agent.env" ] && source "$HOME/.arbfinder-agent.env"
if [ -z "$ARBFINDER_AGENT_URL" ] || [ -z "$ARBFINDER_AGENT_TOKEN" ]; then
  echo "Set up the agent first. Create ~/.arbfinder-agent.env containing:"
  echo "  export ARBFINDER_AGENT_URL='https://<your-app>.fly.dev'"
  echo "  export ARBFINDER_AGENT_TOKEN='<the token you set as a Fly secret>'"
  echo
  echo "Then double-click this file again."
  exit 1
fi

echo "▶ Updating…"
git pull origin "$BRANCH" 2>/dev/null || echo "  (couldn't update — using what you have)"

if [ ! -d .venv ]; then
  echo "▶ First-time setup: creating a Python environment…"
  python3 -m venv .venv || { echo "Need Python 3."; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -r requirements.txt

echo "▶ Starting the agent for $ARBFINDER_AGENT_URL"
echo "  Leave this window open. It runs each scan your phone triggers."
echo
exec python -m arbfinder.agent
