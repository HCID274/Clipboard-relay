# Clipboard Relay（剪贴板中继）

Clipboard Relay 使用 FastAPI 把浏览器输入的文本转发给已注册的 macOS 或 Windows
剪贴板 Agent。设备可以通过安装脚本自助注册，服务端会把设备清单持久化到 JSON 文件。

## 服务端

服务端使用 `RELAY_PASSWORD` 作为共享密码，并继续通过 `X-API-Key` 请求头传输。迁移期间可以
同时保留旧 `API_KEY`；任一凭据都有效。两台旧设备切换完成后，请删除 `API_KEY` 并重启服务。

```bash
cd server
RELAY_PASSWORD='correct-horse-battery-staple-2026' MAX_DEVICES=10 \
  uv run uvicorn app:app --host 127.0.0.1 --port 18080 --workers 1
```

共享密码只允许使用 ASCII 字符。管理员应当使用足够长的随机密码或随机英文词组，并且客户端与
服务端必须配置完全相同的值。

服务端必须保持单 worker。默认设备文件是 `server/devices.json`，也可以通过
`DEVICES_FILE` 指定路径。损坏文件会触发警告并按空清单启动。

## 接口

- `POST /api/devices/register` 注册或确认设备。
- `GET /api/devices` 返回设备记录和在线状态。
- `DELETE /api/devices/{device_id}` 删除设备并关闭其在线连接。
- `POST /api/send` 向在线设备发送文本。
- `GET /api/status` 返回兼容格式的在线状态。
- `WS /ws/agent?device_id=...` 接受已注册 Agent 的连接。

浏览器页面会把密码存入 `localStorage`，并在每个 API 请求中重新发送密码。页面可以动态选择、
查看和删除设备。删除设备只会清理清单和断开当前连接；持有共享密码的设备仍然可以重新注册。

## Agent 安装

请进入 `agent/macos/` 或 `agent/windows/` 并按照对应 README 运行安装脚本。首次安装会读取
hostname 作为设备名建议，用户可以确认或修改；注册成功后，最终 `device_id` 会写入本地配置，
以后启动会直接复用。

完整协议和错误码说明位于 [docs/protocol.md](docs/protocol.md)。
