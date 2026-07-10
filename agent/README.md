# Agent

Per-platform clipboard client implementations live here. Each connects to the
Osaka relay's WebSocket (`wss://clip.hcid274.cn/ws/agent?device_id=...`) and
writes received text to the local user's clipboard.

- `macos/` — Python LaunchAgent (`clipboard_relay_agent`), device_id `mac-china`.
  Tested, currently deployed. See `macos/README.md`.
- `windows/` — not yet implemented. device_id `win-fukuoka`.
