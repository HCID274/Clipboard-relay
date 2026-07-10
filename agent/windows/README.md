# Clipboard Relay Agent

Windows user-session Clipboard Relay Agent for `wss://clip.hcid274.cn/ws/agent?device_id=win-fukuoka`.

## Setup

```cmd
uv venv .venv
uv pip install -r requirements.txt --python .venv\Scripts\python.exe
copy config.example.json config.json
```

Edit `config.json` and replace `api_key` with the shared key.

`max_reconnect_attempts` limits consecutive short or failed reconnect cycles. A connection that stays up for at least 60 seconds is treated as stable and resets the counter.

## Foreground Test

```cmd
.venv\Scripts\python.exe agent.py
```

Expected log output:

```text
connected to wss://clip.hcid274.cn/ws/agent?device_id=win-fukuoka
```

Send a test message:

```cmd
curl -X POST https://clip.hcid274.cn/api/send ^
  -H "Content-Type: application/json" ^
  -H "X-API-Key: shared-key" ^
  -d "{\"target\":\"win-fukuoka\",\"text\":\"hello clipboard relay\"}"
```

Then paste in Windows and confirm the clipboard contains `hello clipboard relay`.

## Background Startup

```cmd
install_task.cmd
schtasks /Query /TN "ClipboardRelayAgent"
```

The scheduled task starts `.venv\Scripts\pythonw.exe agent.py` when the current user logs in.

To uninstall:

```cmd
uninstall_task.cmd
```

## Logs

Logs are written to:

```text
%APPDATA%\ClipboardRelay\agent.log
```

The log records text length only, not clipboard contents.
