#!/bin/bash
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  retail-arbitrage-finder — one-double-click start                        │
# │                                                                          │
# │  Double-click this file in Finder. It updates to the latest code, sets   │
# │  everything up the first time, and opens the dashboard in your browser.  │
# │  No typing. Close the Terminal window it opens to stop the dashboard.    │
# │  (Tip: right-click → Make Alias, drag the alias to your Desktop/Dock.)   │
# └─────────────────────────────────────────────────────────────────────────┘

cd "$(dirname "$0")/.." || exit 1          # repo root (this file lives in scripts/)
REPO="$(pwd)"
BRANCH="claude/retail-arbitrage-finder-tjc9k2"

echo "▶ retail-arbitrage-finder"
echo "  folder: $REPO"
echo

# 1. Pull the latest code (don't abort the launch if you're offline).
if git rev-parse --git-dir >/dev/null 2>&1; then
  echo "▶ Getting the latest version…"
  git pull origin "$BRANCH" || echo "  (couldn't update — starting with what you have)"
  echo
fi

# 2. Virtualenv + dependencies (created once, then reused).
if [ ! -d .venv ]; then
  echo "▶ First-time setup: creating a Python environment…"
  python3 -m venv .venv || { echo "Could not create venv — is Python 3 installed?"; exit 1; }
fi
# shellcheck disable=SC1091
source .venv/bin/activate
echo "▶ Checking dependencies…"
pip install -q -r requirements.txt

# 3. Launch — the dashboard opens your browser automatically.
echo
echo "▶ Starting the dashboard. A browser tab will open."
echo "  Leave this window open while you use it; close it to stop."
echo
python -m arbfinder.dashboard
