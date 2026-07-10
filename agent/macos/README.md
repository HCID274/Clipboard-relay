# Clipboard Relay Agent for Mac

User-level macOS agent that connects to the Osaka Clipboard Relay WebSocket and writes received text to the current login user's clipboard.

## Paths

- Project: `agent/macos/` in this repo (previously a standalone project at
  `~/Documents/Codex/01_projects/clipboard-relay-agent-mac`)
- Config: `~/Library/Application Support/ClipboardRelay/config.json`
- Status: `~/Library/Application Support/ClipboardRelay/status.json`
- Log: `~/Library/Logs/ClipboardRelay/agent.log` with 1 MB rotation and 3 backups
- LaunchAgent: `~/Library/LaunchAgents/com.clipboardrelay.agent.plist`

## Setup

```bash
cd agent/macos
~/.local/bin/uv sync
mkdir -p "$HOME/Library/Application Support/ClipboardRelay"
cp config.example.json "$HOME/Library/Application Support/ClipboardRelay/config.json"
```

Edit `api_key` in `~/Library/Application Support/ClipboardRelay/config.json`.

## Foreground Test

```bash
~/.local/bin/uv run python -m clipboard_relay_agent
```

Send a route test:

```bash
curl -X POST https://clip.hcid274.cn/api/send \
  -H "Content-Type: application/json" \
  -H "X-API-Key: existing-shared-key" \
  -d '{"target":"mac-china","text":"mac route test"}'
```

Check:

```bash
pbpaste
```

## Diagnostics

Check the current LaunchAgent process:

```bash
launchctl print "gui/$(id -u)/com.clipboardrelay.agent"
```

Check the latest connection state written by the agent:

```bash
cat "$HOME/Library/Application Support/ClipboardRelay/status.json"
```

The status file records the target `device_id`, the WebSocket URL, the latest event, the process ID, and whether the agent currently believes the WebSocket is connected.

## Background Install

```bash
scripts/install_launchagent.sh
launchctl list | grep clipboardrelay
tail -n 100 "$HOME/Library/Logs/ClipboardRelay/agent.log"
```

The LaunchAgent is installed as a background process with low-priority I/O and a 30-second launch throttle.

## Uninstall

```bash
scripts/uninstall_launchagent.sh
```
