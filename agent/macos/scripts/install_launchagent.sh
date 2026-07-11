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
  echo "Created $CONFIG_PATH from config.example.json."
fi

"$UV_BIN" python install 3.12
"$UV_BIN" sync --frozen --project "$PROJECT_DIR"

password_needs_setup() {
  "$PROJECT_DIR/.venv/bin/python" - "$CONFIG_PATH" <<'PY'
import sys
from pathlib import Path

from clipboard_relay_agent.config import ConfigError, config_needs_password

try:
    needs_password = config_needs_password(Path(sys.argv[1]))
except ConfigError as exc:
    print(f"Cannot inspect config password: {exc}", file=sys.stderr)
    raise SystemExit(2)

raise SystemExit(0 if needs_password else 1)
PY
}

set +e
password_needs_setup
password_status=$?
set -e

if [[ $password_status -eq 0 ]]; then
  echo "Enter the Clipboard Relay shared password. Your input will not be displayed."
  while true; do
    printf "Password: "
    IFS= read -r -s password
    printf "\n"

    if printf '%s' "$password" | "$PROJECT_DIR/.venv/bin/python" -c '
import sys
from pathlib import Path

from clipboard_relay_agent.config import ConfigError, set_password

try:
    set_password(Path(sys.argv[1]), sys.stdin.read())
except ConfigError as exc:
    print(f"Password was not saved: {exc}", file=sys.stderr)
    raise SystemExit(1)
' "$CONFIG_PATH"
    then
      break
    fi

    echo "Please enter a non-placeholder ASCII password."
  done
elif [[ $password_status -ne 1 ]]; then
  exit "$password_status"
fi

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
