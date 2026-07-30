#!/bin/bash
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  Install the Mac agent as an always-on background service.               │
# │                                                                          │
# │  Double-click ONCE. After that the agent starts automatically every      │
# │  time you log in, runs invisibly in the background (no Terminal window),  │
# │  and restarts itself if it crashes — so you can scan from your phone      │
# │  any time your Mac is awake.                                             │
# │                                                                          │
# │  To turn it off later, double-click uninstall-agent-service.command.     │
# └─────────────────────────────────────────────────────────────────────────┘

set -e
REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.arbfinder.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/arbfinder-agent.log"

# 1. Make sure the agent is configured (URL + token) before installing.
if [ ! -f "$HOME/.arbfinder-agent.env" ]; then
  echo "✖ Set up the agent first — ~/.arbfinder-agent.env is missing."
  echo "  Create it with two lines:"
  echo "    export ARBFINDER_AGENT_URL='https://<your-app>.fly.dev'"
  echo "    export ARBFINDER_AGENT_TOKEN='<the token you set as a Fly secret>'"
  echo "  Then double-click this again."
  exit 1
fi

# 2. First-time venv so the service has something to run.
if [ ! -x "$REPO/.venv/bin/python" ]; then
  echo "▶ First-time setup: creating the Python environment…"
  ( cd "$REPO" && python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt )
fi

# 3. Write the LaunchAgent plist (repo path templated in).
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/Library/Logs"
cat > "$PLIST" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/bash</string>
        <string>$REPO/scripts/agent-service.sh</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$REPO</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG</string>
    <key>StandardErrorPath</key>
    <string>$LOG</string>
</dict>
</plist>
PLIST_EOF

# 4. (Re)load it.
launchctl unload "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"

echo "✔ Installed. The agent now starts every time you log in and runs in the background."
echo "  • Logs:      $LOG"
echo "  • Turn off:  double-click uninstall-agent-service.command"
echo
echo "Open your hosted site — the banner should show 🟢 Mac agent connected within ~30s."
