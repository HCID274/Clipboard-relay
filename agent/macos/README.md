# Mac 剪贴板中继 Agent

该 Agent 使用用户级 LaunchAgent 接收剪贴板文本。配置文件位于
`~/Library/Application Support/ClipboardRelay/config.json`，状态文件位于同一目录，轮转日志位于
`~/Library/Logs/ClipboardRelay/agent.log`。

## 安装与注册

```bash
cd agent/macos
scripts/install_launchagent.sh
```

首次运行会复制 `config.example.json` 并要求用户编辑其中的 `password`。用户编辑后再次运行安装
脚本，脚本会显示从 hostname 生成的设备名建议；用户可以回车确认或输入新名称。注册成功后，
服务端返回的 `device_id` 会保存到配置文件，并且 LaunchAgent 才会启动。

旧配置中的 `api_key` 字段仍可读取，便于迁移；建议将该字段改名为 `password`。已有
`device_id` 时，安装和启动会直接复用该身份，不再询问设备名。正常启动仍会向注册接口确认
该设备存在，密码错误、设备数达到上限或网络失败都会显示明确错误并阻止后台安装。

## 前台测试与诊断

```bash
~/.local/bin/uv run python -m clipboard_relay_agent --register-only
~/.local/bin/uv run python -m clipboard_relay_agent
launchctl print "gui/$(id -u)/com.clipboardrelay.agent"
tail -n 100 "$HOME/Library/Logs/ClipboardRelay/agent.log"
```

卸载命令是 `scripts/uninstall_launchagent.sh`。日志只记录文本长度，不记录剪贴板正文或密码。
