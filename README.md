# Clipboard Relay

FastAPI relay for sending text from an Android browser page to connected desktop clipboard agents.

## Layout

- `server/` = FastAPI relay service and browser page.
- `agent/` = cross-platform clipboard clients.
- `docs/` = protocol contracts and deployment notes.

## Devices

- `win-fukuoka` = 福冈 Windows
- `mac-china` = 中国大陆 Mac

## Endpoints

- `GET /` serves the browser page.
- `GET /health` returns `{"ok": true}`.
- `POST /api/send` accepts `{"target":"win-fukuoka","text":"..."}` or `{"target":"mac-china","text":"..."}` with `X-API-Key`.
- `WS /ws/agent?device_id=win-fukuoka` accepts the Windows agent connection with `X-API-Key`.
- `WS /ws/agent?device_id=mac-china` accepts the Mac agent connection with `X-API-Key`.

The browser page stores the user-entered API key in `localStorage` under
`clipboardRelayApiKey`, stores the selected target under `clipboardRelayTarget`,
and lets the user clear the key from the page. The server still requires
`X-API-Key` for every send request.

The server pushes messages to the agent in this format:

```json
{"type":"clipboard","text":"..."}
```

Run locally:

```bash
cd server
uv run uvicorn app:app --host 127.0.0.1 --port 18080 --workers 1
```
