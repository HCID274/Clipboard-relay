# Mac 剪贴板中继 Agent

用户级 macOS Agent，连接大阪剪贴板中继服务的 WebSocket，把收到的文本写入当前
登录用户的剪贴板。

## 路径

- 项目位置：本仓库的 `agent/macos/`（此前是独立项目，位于
  `~/Documents/Codex/01_projects/clipboard-relay-agent-mac`，已合并进 monorepo）
- 配置文件：`~/Library/Application Support/ClipboardRelay/config.json`
- 状态文件：`~/Library/Application Support/ClipboardRelay/status.json`
- 日志：`~/Library/Logs/ClipboardRelay/agent.log`，1 MB 轮转，保留 3 份
- LaunchAgent：`~/Library/LaunchAgents/com.clipboardrelay.agent.plist`

## 安装配置

```bash
cd agent/macos
~/.local/bin/uv sync
mkdir -p "$HOME/Library/Application Support/ClipboardRelay"
cp config.example.json "$HOME/Library/Application Support/ClipboardRelay/config.json"
```

编辑 `~/Library/Application Support/ClipboardRelay/config.json` 里的 `api_key`。

## 前台测试

```bash
~/.local/bin/uv run python -m clipboard_relay_agent
```

发一条路由测试：

```bash
curl -X POST https://clip.hcid274.cn/api/send \
  -H "Content-Type: application/json" \
  -H "X-API-Key: existing-shared-key" \
  -d '{"target":"mac-china","text":"mac route test"}'
```

检查结果：

```bash
pbpaste
```

## 诊断排查

查看当前 LaunchAgent 进程：

```bash
launchctl print "gui/$(id -u)/com.clipboardrelay.agent"
```

查看 Agent 写入的最新连接状态：

```bash
cat "$HOME/Library/Application Support/ClipboardRelay/status.json"
```

状态文件记录了目标 `device_id`、WebSocket 地址、最近一次事件、进程 ID，以及
Agent 当前认为的连接状态。

## 后台安装

```bash
scripts/install_launchagent.sh
launchctl list | grep clipboardrelay
tail -n 100 "$HOME/Library/Logs/ClipboardRelay/agent.log"
```

LaunchAgent 会以后台进程方式安装，低 I/O 优先级，30 秒启动节流。

## 卸载

```bash
scripts/uninstall_launchagent.sh
```
