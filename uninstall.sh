#!/usr/bin/env bash
set -euo pipefail

LABEL="local.granola-export"
PLIST_DEST="$HOME/Library/LaunchAgents/local.granola-export.plist"
SCRIPT_DEST="$HOME/scripts/granola-export.py"

echo "Uninstalling granola-export..."

# --- Unload the agent ---
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null && \
    echo "  LaunchAgent unloaded" || \
    echo "  LaunchAgent was not loaded"

# --- Remove installed files ---
rm -f "$PLIST_DEST" && echo "  Removed $PLIST_DEST"
rm -f "$SCRIPT_DEST" && echo "  Removed $SCRIPT_DEST"

echo ""
echo "Done. Your exported notes were not touched."
