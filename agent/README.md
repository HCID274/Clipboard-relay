# Clipboard Relay Agents

`macos/` 和 `windows/` 分别包含两个桌面剪贴板 Agent。两个 Agent 都从配置文件读取服务地址和
共享密码，调用 `POST /api/devices/register` 后再连接 `/ws/agent`。

首次安装时，安装流程会以 hostname 提供设备名建议，并允许用户确认或修改。服务端注册成功后，
Agent 会把规范化后的 `device_id` 持久化到本地配置；后续启动会复用该身份，不会根据用户名或
变化后的 hostname 重新生成。

各平台的具体安装步骤请查看 [macOS README](macos/README.md) 和
[Windows README](windows/README.md)。协议契约请查看 [协议文档](../docs/protocol.md)。
