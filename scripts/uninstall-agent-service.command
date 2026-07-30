#!/bin/bash
# ┌─────────────────────────────────────────────────────────────────────────┐
# │  Turn OFF the always-on Mac agent service.                               │
# │                                                                          │
# │  Double-click to stop the background agent and prevent it starting at    │
# │  login. Your settings (~/.arbfinder-agent.env) are left untouched, so    │
# │  you can re-install any time with install-agent-service.command, or run  │
# │  it manually by double-clicking agent.command.                           │
# └─────────────────────────────────────────────────────────────────────────┘

LABEL="com.arbfinder.agent"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -f "$PLIST" ]; then
  echo "The service isn't installed — nothing to remove."
  exit 0
fi

launchctl unload -w "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "✔ Stopped. The agent will no longer start at login."
echo "  Your site banner will show 🔴 No Mac agent connected until you start it again."
