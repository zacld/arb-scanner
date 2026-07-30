#!/bin/bash
# Lean runner for the Mac agent, launched by launchd (the always-on service
# installed by scripts/install-agent-service.command). Unlike scripts/agent.command
# this does NOT git-pull or pip-install — those belong to first-time setup, not
# every automatic restart. launchd's KeepAlive re-runs this if it exits.
#
# It expects the venv and ~/.arbfinder-agent.env to already exist (the installer
# checks for the env file first).

cd "$(dirname "$0")/.." || exit 1

# Saved hosted URL + token (kept out of the repo).
[ -f "$HOME/.arbfinder-agent.env" ] && source "$HOME/.arbfinder-agent.env"

if [ -z "$ARBFINDER_AGENT_URL" ] || [ -z "$ARBFINDER_AGENT_TOKEN" ]; then
  echo "$(date '+%F %T') agent-service: ~/.arbfinder-agent.env missing URL/token." >&2
  echo "  Create it with ARBFINDER_AGENT_URL and ARBFINDER_AGENT_TOKEN, then reload." >&2
  sleep 30   # don't hot-loop under KeepAlive while it's misconfigured
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "$(date '+%F %T') agent-service: no .venv found — run scripts/agent.command once first." >&2
  sleep 30
  exit 1
fi

echo "$(date '+%F %T') agent-service: starting agent for $ARBFINDER_AGENT_URL"
exec .venv/bin/python -m arbfinder.agent
