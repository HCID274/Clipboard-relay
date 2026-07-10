# Agent（客户端）

各平台的剪贴板客户端实现都放在这里。每个 Agent 连接大阪中继服务的 WebSocket
（`wss://clip.hcid274.cn/ws/agent?device_id=...`），把收到的文本写入本机剪贴板。
共用的协议契约见 [docs/protocol.md](../docs/protocol.md)。

- `macos/` —— Python LaunchAgent（`clipboard_relay_agent`），device_id 为
  `mac-china`。已测试，正在运行中。详见 `macos/README.md`。
- `windows/` —— Python 脚本 + 计划任务（Task Scheduler），device_id 为
  `win-fukuoka`。已测试，正在运行中。详见 `windows/README.md`。
