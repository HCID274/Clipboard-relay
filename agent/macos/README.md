# Mac 剪贴板中继 Agent

该 Agent 使用用户级 LaunchAgent 接收剪贴板文本。配置文件位于
`~/Library/Application Support/ClipboardRelay/config.json`，状态文件位于同一目录，轮转日志位于
`~/Library/Logs/ClipboardRelay/agent.log`。

## 安装与注册

在 Finder 中双击 `一键安装.command` 即可完成安装。该入口会打开终端，首次安装时会隐藏回显地要求输入
服务端共享密码，然后自动安装依赖、注册设备并安装 LaunchAgent。

也可以在终端中运行：

```bash
cd agent/macos
scripts/install_launchagent.sh
```

首次运行会自动复制 `config.example.json`，并且安装脚本会隐藏回显地要求输入 `password`，因此用户
不需要手动编辑任何配置文件。密码已填写且有效时，脚本会跳过密码提示，因而可以安全地重复运行。
脚本会把从 hostname 生成的建议设备名**预填进输入行**（macOS 终端用 readline），可直接回车确认，
也可用方向键 / 退格修改后再回车。注册成功后，服务端返回的 `device_id` 会保存到配置文件，
并且 LaunchAgent 才会启动。
`password` 只允许使用 ASCII 字符，并且管理员应当配置足够长的随机密码或随机英文词组。

旧配置中的 `api_key` 字段仍可读取，便于迁移；建议将该字段改名为 `password`。已有
`device_id` 时，安装和启动会直接复用该身份，不再询问设备名。正常启动仍会向注册接口确认
该设备存在，密码错误、设备数达到上限或网络失败都会显示明确错误并阻止后台安装。

## 单元测试

在本目录执行（不要从仓库根乱指路径）：

```bash
cd agent/macos
uv run pytest -q
```

## 前台测试与诊断

```bash
~/.local/bin/uv run python -m clipboard_relay_agent --register-only
~/.local/bin/uv run python -m clipboard_relay_agent
launchctl print "gui/$(id -u)/com.clipboardrelay.agent"
tail -n 100 "$HOME/Library/Logs/ClipboardRelay/agent.log"
```

卸载命令是 `scripts/uninstall_launchagent.sh`。日志只记录文本长度，不记录剪贴板正文或密码。
