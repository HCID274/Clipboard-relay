# Clipboard Relay（剪贴板中继）

一个 FastAPI 中继服务，把手机浏览器页面输入的文本发送给已连接的桌面剪贴板 Agent。

## 目录结构

- `server/` = FastAPI 中继服务和浏览器发送页面。
- `agent/` = 各平台的剪贴板客户端。
- `docs/` = 协议契约和部署说明。

## 设备清单

- `win-fukuoka` = 福冈 Windows
- `mac-china` = 中国大陆 Mac

完整协议细节见 [docs/protocol.md](docs/protocol.md)。

## 接口

- `GET /` 返回浏览器发送页面。
- `GET /health` 返回 `{"ok": true}`。
- `POST /api/send` 接受 `{"target":"win-fukuoka","text":"..."}` 或
  `{"target":"mac-china","text":"..."}`，需带 `X-API-Key`。
- `WS /ws/agent?device_id=win-fukuoka` 接受 Windows Agent 的连接，需带 `X-API-Key`。
- `WS /ws/agent?device_id=mac-china` 接受 Mac Agent 的连接，需带 `X-API-Key`。

浏览器页面把用户输入的密钥存在 `localStorage` 的 `clipboardRelayApiKey` 里，
把选择的目标设备存在 `clipboardRelayTarget` 里，并支持在页面上清除密钥。
无论如何，服务端每次发送请求都仍然要求 `X-API-Key`。

服务端推给 Agent 的消息格式：

```json
{"type":"clipboard","text":"..."}
```

本地运行：

```bash
cd server
uv run uvicorn app:app --host 127.0.0.1 --port 18080 --workers 1
```
