# 剪贴板中继协议

服务端、浏览器和 macOS/Windows Agent 共同遵守本协议。服务端固定使用单进程、单 worker，
因为 WebSocket 在线状态和设备文件写锁都位于进程内存中。

## 鉴权与配置

所有受保护的 HTTP 请求和 WebSocket 连接都携带 `X-API-Key: <共享密码>`。服务端使用
`hmac.compare_digest()` 比较凭据，密码不会出现在 URL 中。生产环境必须使用 HTTPS/WSS，
而且日志不得记录密码。

服务端优先使用明文环境变量 `RELAY_PASSWORD`。迁移期间，旧环境变量 `API_KEY` 与
`RELAY_PASSWORD` 会同时有效；两台旧设备都切换到新密码后，管理员需要删除 `API_KEY`
并重启服务。两个变量都未配置时，HTTP 返回 `500`，WebSocket 以 `1008` 关闭；密码错误时，
HTTP 返回 `401`，WebSocket 以 `1008` 关闭。

`RELAY_PASSWORD`、迁移期 `API_KEY` 和客户端密码只允许使用 ASCII 字符。任何非 ASCII 候选值
都会被视为密码错误，并且某个无效候选值不会中断另一个迁移期凭据的校验。

人类可记忆密码的熵低于长随机 API key。当前实现不包含密码哈希和登录限速，因此该方案只适合
小规模可信环境，不适合直接大规模暴露到公网。

相关环境变量如下：

| 变量 | 默认值 | 说明 |
|---|---|---|
| `RELAY_PASSWORD` | 空 | 新共享密码 |
| `API_KEY` | 空 | 迁移期旧凭据；迁移结束后删除 |
| `MAX_DEVICES` | `10` | 已注册设备上限，必须是正整数 |
| `DEVICES_FILE` | `server/devices.json` | 设备清单文件路径 |

## 设备清单

设备清单以 JSON 文件持久化，每条记录包含 `device_id`、`created_at` 和 `last_active`。
文件写入采用同目录临时文件加原子替换。注册、删除、WebSocket 连接成功与断开更新使用同一把
`asyncio.Lock`。文件损坏时，服务端会记录警告并以空清单启动。

`device_id` 会转换为小写，并且只允许 3 至 32 位小写字母、数字或连字符。仓库中的初始清单
预置 `win-fukuoka` 与 `mac-china`，以支持旧设备平滑迁移。在线状态只保存在内存中。
`last_active` 在**新设备注册成功**、**当前 WebSocket 连接成功**以及**当前有效连接断开**时写入
并持久化；已存在设备再次调用注册接口不会刷新 `last_active`。
`GET /api/devices` 对 `online: true` 的设备返回**当前时间**作为 `last_active`（只读展示，
不写盘），以便页面轮询时可见“仍在活跃”；离线设备返回文件中持久化的时间戳。

## HTTP 接口

### `GET /` 与 `GET /health`

`GET /` 返回公开浏览器页面。`GET /health` 返回 `{"ok": true}`。这两个接口不要求鉴权。

### `POST /api/devices/register`

请求体为 `{"device_id":"my-laptop"}`。新设备注册成功时返回 `200` 和完整设备记录。
已存在的 ID 返回原记录且不重复新增。非法 ID 返回 `400`。达到 `MAX_DEVICES` 后，新 ID 返回
`403 {"detail":"已达设备数上限"}`，但已注册设备仍然可以确认注册和重连。

### `GET /api/devices`

该接口返回持久字段和实时在线状态。当 `online` 为 `true` 时，响应中的 `last_active` 为
请求时刻的服务器当前时间（不写盘）；离线设备的 `last_active` 为最近一次注册 / 连接 /
断开时持久化的值。

```json
[
  {
    "device_id": "win-fukuoka",
    "created_at": "2026-07-11T00:00:00Z",
    "last_active": "2026-07-11T00:00:00Z",
    "online": true,
    "latency_ms": 48
  }
]
```

`latency_ms` 是**服务器到该 Agent** 的应用层往返时延（毫秒）。服务端定期向在线
Agent 发送 `{"type":"ping"}`，Agent 立即回复 `{"type":"pong"}`；列表接口返回最近一次
成功测得的缓存值。离线设备、尚未测到或测速超时的设备为 `null`。该字段**不写盘**。

### `DELETE /api/devices/{device_id}`

该接口删除设备记录并腾出上限名额。在线设备会立即收到 `1000` 关闭帧并断开。不存在的设备
返回 `404`。删除操作不是安全吊销，因为持有共享密码的客户端可以再次注册；真正的访问控制
边界仍然是共享密码。

### `POST /api/send`

请求体为 `{"target":"my-laptop","text":"..."}`。成功返回 `200 {"ok":true}`；目标非法
或文本为空返回 `400`；目标离线返回 `503`。服务端不会存储或排队文本。

### `GET /api/status`

该兼容接口返回 `{"devices":{"win-fukuoka":true,"mac-china":false}}`。设备管理页面使用
信息更完整的 `GET /api/devices`。

## WebSocket：`/ws/agent`

Agent 在成功调用注册接口后连接：

```text
wss://clip.hcid274.cn/ws/agent?device_id=<已注册设备 ID>
```

密码错误、参数非法或设备未注册时，服务端以 `1008` 关闭。相同 `device_id` 的新连接会以
`1000` 关闭旧连接并取代它。服务端推送消息格式为：

```json
{"type":"clipboard","text":"..."}
```

RTT 探测（服务端 → Agent → 服务端）：

```json
{"type":"ping","id":"<probe-uuid>","t":<服务器 monotonic 时间戳>}
{"type":"pong","id":"<probe-uuid>","t":<原样回传>}
```

Agent 收到 `ping` 后应立即回复 `pong`，并**原样回传** `id` 与 `t`，不要写入剪贴板。
服务端只接受同时满足下列条件的 `pong`：来自该 `device_id` 的**当前** WebSocket、
且 `id` 与本次探测一致。错误 / 迟到 / 旧连接的 `pong` 一律忽略。

WebSocket 连接成功（accept 且设备已注册）后，服务端立即更新并持久化该设备的 `last_active`，
并尽快发起一次延迟探测。服务端通过 `receive_text()` 消费 Agent 上行（含 `pong`）并检测断线。
只有当前有效连接断开时，服务端才会移除内存连接并再次更新该设备的 `last_active`；被同名新连接
替换的旧连接不会覆盖当前状态（包括新连接写入的 `last_active`）。

## Agent 首次配置

安装脚本读取本地配置中的 `device_id`。若该字段存在，脚本直接复用；若该字段不存在，脚本以
hostname 生成建议值，并允许用户回车确认或手动修改。Agent 调用注册接口成功后，以原子写入方式
把服务端返回的规范化 ID 保存到配置文件，再建立 WebSocket。密码错误、设备上限和网络错误都会
显示明确的终端错误。旧配置 URL 中的 `device_id` 会作为已保存身份复用，并会迁移到独立字段。
每次进程启动都会用已保存 ID 调用注册接口进行重连确认。

## 当前约束

- 服务端只能运行一个 worker，因为设备文件锁和连接表不跨进程共享。
- 服务端没有消息队列、限流、密码哈希和请求体大小限制。
- 所有设备共用一个密码，两台物理设备使用同一 ID 时，后连接者会取代先连接者。
