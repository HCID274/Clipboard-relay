#!/usr/bin/env bash
set -euo pipefail

PLIST_PATH="$HOME/Library/LaunchAgents/com.clipboardrelay.agent.plist"
LABEL="com.clipboardrelay.agent"

launchctl stop "$LABEL" 2>/dev/null || true
launchctl unload "$PLIST_PATH" 2>/dev/null || true
rm -f "$PLIST_PATH"

echo "Uninstalled LaunchAgent: $PLIST_PATH"
