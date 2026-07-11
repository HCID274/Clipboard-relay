# Windows 剪贴板中继 Agent

该 Agent 在 Windows 用户会话中运行，并通过计划任务保持自启动。日志位于
`%APPDATA%\ClipboardRelay\agent.log`，日志只记录文本长度，不记录剪贴板正文或密码。

## 安装与注册

```cmd
cd agent\windows
uv venv .venv
uv pip install -r requirements.txt --python .venv\Scripts\python.exe
copy config.example.json config.json
```

请先把 `config.json` 中的 `password` 改为服务端共享密码，然后运行：

```cmd
install_task.cmd
```

安装脚本会先执行交互式注册。配置中没有 `device_id` 时，Agent 会显示从 hostname 生成的建议值，
用户可以回车确认或输入新名称。注册成功后，最终 ID 会保存到 `config.json`，计划任务才会创建。
已有 `device_id` 时，Agent 会直接复用该身份。旧 `api_key` 字段仍可读取，但建议迁移为
`password`。密码错误、设备数达到上限和网络错误会显示明确提示。

## 前台测试与后台管理

```cmd
.venv\Scripts\python.exe agent.py --register-only
.venv\Scripts\python.exe agent.py
schtasks /Query /TN "ClipboardRelayAgent"
```

连接稳定运行满 60 秒后，Agent 会重置重连计数。卸载命令是 `uninstall_task.cmd`。
