# Clipboard Relay Protocol

This is the wire contract between the server (`server/`) and any agent
(`agent/macos/`, `agent/windows/`). Any change here must be rolled out to the
server and every agent together — they are not independently versioned.

Source of truth for the actual behavior is `server/app.py`. If this doc and
the code disagree, the code wins — fix this doc.

## Actors

- **Browser** — sends text via `POST /api/send`.
- **Server** — single FastAPI process, holds one WebSocket per connected
  agent in memory (no persistence, no queue).
- **Agent** — a per-device background process that holds the `/ws/agent`
  connection open and writes incoming text to the local clipboard.

## Devices

Device IDs are hardcoded in `server/app.py` (`DEVICES` dict), not configurable
via environment yet:

| device_id     | label          |
|---------------|----------------|
| `win-fukuoka` | 福冈 Windows    |
| `mac-china`   | 中国大陆 Mac    |

A `target`/`device_id` not in this dict is rejected everywhere it's used.

## Auth

Every request (HTTP and WebSocket) must carry:

```
X-API-Key: <shared secret>
```

The server compares it with `hmac.compare_digest()` against the `API_KEY` env
var. There is a single shared key for all devices — no per-device keys.

- If `API_KEY` is unset on the server: HTTP returns `500`, WebSocket closes
  immediately with code `1008`.
- If the key is present but wrong: HTTP returns `401`, WebSocket closes with
  code `1008`.
- **Note:** a bad `device_id` also closes with code `1008` — the close code
  alone does not tell you whether auth or device_id failed.

## HTTP API

### `GET /`
Returns `server/static/index.html`. No auth required.

### `GET /health`
Returns `{"ok": true}`. No auth required. Used for systemd/docker/nginx
health checks.

### `POST /api/send`

Headers: `X-API-Key: <key>`, `Content-Type: application/json`

Body:
```json
{"target": "win-fukuoka", "text": "..."}
```

Responses:

| Status | Condition |
|---|---|
| `200 {"ok": true}` | Delivered to the connected agent |
| `400 invalid target` | `target` missing or not in `DEVICES` |
| `400 text is empty` | `text` missing, not a string, or all whitespace |
| `401 invalid API key` | Key missing or wrong |
| `500 API_KEY is not configured` | Server has no `API_KEY` set |
| `503 target device is not connected` | No agent currently holds that `device_id`'s WebSocket |

Notes:
- `text` is only checked with `.strip()` for emptiness — the value actually
  forwarded to the agent is the **original, unstripped** string.
- The server never stores the text; if the agent isn't connected, the
  message is dropped, not queued.

## WebSocket: `/ws/agent`

Connect:
```
wss://clip.hcid274.cn/ws/agent?device_id=<win-fukuoka|mac-china>
```
Header: `X-API-Key: <shared secret>`

Server-side validation order on connect:
1. `X-API-Key` must match `API_KEY` → else close `1008`
2. `device_id` query param must be a key in `DEVICES` → else close `1008`
3. Accept the connection, store it keyed by `device_id`

**Replacement behavior:** if a new connection arrives for a `device_id` that
already has one open, the old connection is closed with code `1000` (normal
closure) and replaced. Only one live connection per `device_id` at a time.

**Message pushed to the agent** (server → agent only; the server does not
expect any particular message content back):
```json
{"type": "clipboard", "text": "..."}
```

**Keeping the connection alive:** the server calls `receive_text()` in a loop
purely to detect disconnects — it ignores whatever the agent sends. Agents
may send periodic pings/text as a heartbeat; the protocol does not depend on
the content.

**Disconnect:** on `WebSocketDisconnect`, the server removes that `device_id`
from its in-memory map — the target then shows as "not connected" for
`/api/send` (`503`) until a new agent connection replaces it.

## Constraints (current, not aspirational)

- Single process / single worker only. Connections live in-process memory —
  multiple workers or replicas would let `/api/send` land on a worker with no
  knowledge of the agent's connection. Don't scale this horizontally without
  first moving connection state to something shared (e.g. Redis pub/sub).
- No message queueing — an offline agent means the message is lost, not
  delayed.
- No rate limiting, no request body size limit.
- No per-device API keys — one shared key for everything.
