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
- `GET /api/status` 返回全部设备当前的在线布尔状态，需带 `X-API-Key`。
- `WS /ws/agent?device_id=win-fukuoka` 接受 Windows Agent 的连接，需带 `X-API-Key`。
- `WS /ws/agent?device_id=mac-china` 接受 Mac Agent 的连接，需带 `X-API-Key`。

浏览器页面把用户输入的密钥存在 `localStorage` 的 `clipboardRelayApiKey` 里，
把选择的目标设备存在 `clipboardRelayTarget` 里，并支持在页面上清除密钥。
无论如何，服务端每次发送请求都仍然要求 `X-API-Key`。

浏览器页面会在密钥非空时查询并约每四秒轮询 `/api/status`，从而在目标设备下方独立展示
连接状态。页面会区分已连接、客户端未连接、密钥错误、服务端未配置 `API_KEY` 和中继服务器
不可达；设备下拉框通过“（在线）”或“（离线）”文字后缀标注每台设备的状态，不使用颜色区分选项。

服务端推给 Agent 的消息格式：

```json
{"type":"clipboard","text":"..."}
```

本地运行：

```bash
cd server
uv run uvicorn app:app --host 127.0.0.1 --port 18080 --workers 1
```
