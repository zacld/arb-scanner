#!/bin/bash
# Launch Google Chrome with a remote-debugging port so arbfinder can drive it
# (--via chrome). Uses a dedicated profile so it won't touch your normal Chrome.
#
# macOS: double-click this file (chrome-debug.command), or run it in Terminal.
# Then, IN THAT CHROME WINDOW, browse the retailer once (e.g. open
# https://www.argos.co.uk/search/air-fryer/ ) so it clears the bot check.
# Leave it open, then run:
#   python -m arbfinder scan "air fryer" --source argos --comparator ebay --via chrome
#
# Linux users: swap the path below for `google-chrome` or `chromium`.

CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="$HOME/.arbfinder-chrome-debug"
PORT=9222

if [ ! -x "$CHROME" ]; then
  echo "Google Chrome not found at:"
  echo "  $CHROME"
  echo "Install Chrome, or edit CHROME= in this script to point at it."
  exit 1
fi

echo "Launching Chrome with debugging on port $PORT (profile: $PROFILE)…"
echo "In the window that opens, browse the retailer once, then leave it open."
exec "$CHROME" --remote-debugging-port="$PORT" --user-data-dir="$PROFILE" \
  --no-first-run --no-default-browser-check
