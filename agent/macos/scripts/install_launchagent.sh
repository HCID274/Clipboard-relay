#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
UV_BIN="${UV_BIN:-$HOME/.local/bin/uv}"
CONFIG_DIR="$HOME/Library/Application Support/ClipboardRelay"
CONFIG_PATH="$CONFIG_DIR/config.json"
LOG_DIR="$HOME/Library/Logs/ClipboardRelay"
PLIST_PATH="$HOME/Library/LaunchAgents/com.clipboardrelay.agent.plist"
LABEL="com.clipboardrelay.agent"

if [[ ! -x "$UV_BIN" ]]; then
  echo "uv not found at $UV_BIN. Install uv first or set UV_BIN=/path/to/uv." >&2
  exit 1
fi

mkdir -p "$CONFIG_DIR" "$LOG_DIR" "$HOME/Library/LaunchAgents"

if [[ ! -f "$CONFIG_PATH" ]]; then
  cp "$PROJECT_DIR/config.example.json" "$CONFIG_PATH"
  echo "Created $CONFIG_PATH. Edit password before starting the LaunchAgent." >&2
  exit 1
fi

if grep -Eq "replace-with-(relay-password|existing-shared-key)" "$CONFIG_PATH"; then
  echo "Edit password in $CONFIG_PATH before starting the LaunchAgent." >&2
  exit 1
fi

"$UV_BIN" python install 3.12
"$UV_BIN" sync --frozen --project "$PROJECT_DIR"
"$PROJECT_DIR/.venv/bin/python" -m clipboard_relay_agent --config "$CONFIG_PATH" --register-only

cat > "$PLIST_PATH" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "https://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>$LABEL</string>

  <key>ProgramArguments</key>
  <array>
    <string>$PROJECT_DIR/.venv/bin/python</string>
    <string>-m</string>
    <string>clipboard_relay_agent</string>
    <string>--config</string>
    <string>$CONFIG_PATH</string>
  </array>

  <key>WorkingDirectory</key>
  <string>$PROJECT_DIR</string>

  <key>RunAtLoad</key>
  <true/>

  <key>KeepAlive</key>
  <true/>

  <key>ThrottleInterval</key>
  <integer>30</integer>

  <key>ProcessType</key>
  <string>Background</string>

  <key>LowPriorityIO</key>
  <true/>

  <key>LowPriorityBackgroundIO</key>
  <true/>
</dict>
</plist>
EOF

launchctl unload "$PLIST_PATH" 2>/dev/null || true
launchctl load "$PLIST_PATH"
launchctl start "$LABEL"

echo "Installed LaunchAgent: $PLIST_PATH"
