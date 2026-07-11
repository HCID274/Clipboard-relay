# 剪贴板中继协议

这是服务端（`server/`）与所有 Agent（`agent/macos/`、`agent/windows/`）之间的通信契约。
改这份协议时，服务端和每一个 Agent 必须一起改——它们不是各自独立版本演进的。

以 `server/app.py` 的实际实现为准。如果本文档和代码有出入，以代码为准，并回来修这份文档。

## 参与方

- **浏览器** —— 通过 `POST /api/send` 发送文本。
- **服务端** —— 单个 FastAPI 进程，在内存里为每个已连接的 Agent 保存一条 WebSocket
  连接（没有持久化，没有消息队列）。
- **Agent** —— 每台设备上的一个常驻后台进程，保持 `/ws/agent` 连接，把收到的文本
  写入本机剪贴板。

## 设备清单

`device_id` 目前硬编码在 `server/app.py` 的 `DEVICES` 字典里，还不支持通过环境变量配置：

| device_id     | 说明          |
|---------------|--------------|
| `win-fukuoka` | 福冈 Windows  |
| `mac-china`   | 中国大陆 Mac  |

任何用到 `target`/`device_id` 的地方，只要不在这个字典里，一律被拒绝。

## 鉴权

所有请求（HTTP 和 WebSocket）都必须带：

```
X-API-Key: <共享密钥>
```

服务端用 `hmac.compare_digest()` 和环境变量 `API_KEY` 比较。所有设备共用同一把密钥，
没有按设备区分的密钥。

- 如果服务端 `API_KEY` 未配置：HTTP 返回 `500`，WebSocket 直接以 `1008` 关闭。
- 如果密钥存在但不对：HTTP 返回 `401`，WebSocket 以 `1008` 关闭。
- **注意**：`device_id` 无效时同样以 `1008` 关闭——单看关闭码无法区分是鉴权失败
  还是 device_id 无效。

## HTTP 接口

### `GET /`
返回 `server/static/index.html`。无需鉴权。

### `GET /health`
返回 `{"ok": true}`。无需鉴权。供 systemd/Docker/Nginx 健康检查使用。

### `POST /api/send`

请求头：`X-API-Key: <密钥>`、`Content-Type: application/json`

请求体：
```json
{"target": "win-fukuoka", "text": "..."}
```

响应：

| 状态码 | 触发条件 |
|---|---|
| `200 {"ok": true}` | 已成功推送给对应 Agent |
| `400 invalid target` | `target` 缺失，或不在 `DEVICES` 里 |
| `400 text is empty` | `text` 缺失、不是字符串、或全是空白字符 |
| `401 invalid API key` | 密钥缺失或错误 |
| `500 API_KEY is not configured` | 服务端没有设置 `API_KEY` |
| `503 target device is not connected` | 该 `device_id` 当前没有已连接的 WebSocket |

注意事项：
- 判空只用 `.strip()` 检查，但**真正转发给 Agent 的是原始、未经 strip 的文本**。
- 服务端从不存储文本；如果目标 Agent 未连接，消息直接丢弃，不会排队等待。

### `GET /api/status`

请求头：`X-API-Key: <共享密钥>`

该接口只读取当前进程内存中的 Agent WebSocket 连接，不会修改连接状态，也不会发送消息。
无论设备是否在线，响应中的 `devices` 都会包含 `DEVICES` 设备清单中的全部 `device_id`：

```json
{"devices": {"win-fukuoka": true, "mac-china": false}}
```

响应：

| 状态码 | 触发条件 |
|---|---|
| `200` | 成功返回所有设备的在线布尔状态；`true` 表示该设备当前有已连接的 WebSocket |
| `401 invalid API key` | 密钥缺失或错误 |
| `500 API_KEY is not configured` | 服务端没有设置 `API_KEY` |

浏览器发送页面会在密钥非空时首次查询、切换目标设备时查询，并且约每四秒轮询该接口。
页面会在目标设备下方显示已连接、客户端未连接、密钥错误、服务端配置错误或中继服务器不可达，
并在设备下拉选项的文字后缀显示“（在线）”或“（离线）”。该连接状态区域独立于发送结果区域。

## WebSocket：`/ws/agent`

连接地址：
```
wss://clip.hcid274.cn/ws/agent?device_id=<win-fukuoka|mac-china>
```
请求头：`X-API-Key: <共享密钥>`

服务端连接时的校验顺序：
1. `X-API-Key` 必须匹配 `API_KEY` —— 否则以 `1008` 关闭
2. `device_id` 查询参数必须是 `DEVICES` 里的一个 key —— 否则以 `1008` 关闭
3. 接受连接，按 `device_id` 存入内存

**替换行为**：如果某个 `device_id` 已有一条连接在线，新连接到达时旧连接会以
`1000`（正常关闭）关闭并被替换。同一个 `device_id` 同一时刻只允许一条活跃连接。

**服务端推给 Agent 的消息**（仅服务端 → Agent 方向；服务端不关心 Agent 回传的
内容）：
```json
{"type": "clipboard", "text": "..."}
```

**保持连接存活**：服务端在循环里调用 `receive_text()`，纯粹是为了侦测断开——
它会忽略 Agent 发来的任何内容。Agent 可以定时发心跳（ping/文本），但协议本身
不依赖心跳的具体内容。

**断线处理**：`WebSocketDisconnect` 触发后，服务端会把该 `device_id` 从内存表里
移除——在新的 Agent 连接顶替它之前，`/api/send` 会认为该目标"未连接"（返回
`503`）。

## 当前约束（现状，不是设计目标）

- 只能单进程 / 单 worker。连接状态保存在进程内存里——多 worker 或多副本部署会
  导致 `/api/send` 落到某个不知道该 Agent 连接的 worker 上。要横向扩展，必须先
  把连接状态迁移到共享存储（例如 Redis pub/sub）。
- 没有消息队列——目标 Agent 离线时消息直接丢失，不会延迟送达。
- 没有限流，没有请求体大小限制。
- 没有按设备区分的密钥——所有设备共用一把。
