# Windows 剪贴板中继 Agent

Windows 用户会话下的剪贴板中继 Agent，连接
`wss://clip.hcid274.cn/ws/agent?device_id=win-fukuoka`。

## 安装配置

```cmd
uv venv .venv
uv pip install -r requirements.txt --python .venv\Scripts\python.exe
copy config.example.json config.json
```

编辑 `config.json`，把 `api_key` 换成共享密钥。

连接稳定运行满 60 秒（`STABLE_CONNECTION_SECONDS`）后会重置重连计数；断线后按
`reconnect_seconds` 的间隔无限重试，不会因为反复短暂断线而放弃退出。

## 前台测试

```cmd
.venv\Scripts\python.exe agent.py
```

预期日志输出：

```text
connected to wss://clip.hcid274.cn/ws/agent?device_id=win-fukuoka
```

发一条测试消息：

```cmd
curl -X POST https://clip.hcid274.cn/api/send ^
  -H "Content-Type: application/json" ^
  -H "X-API-Key: shared-key" ^
  -d "{\"target\":\"win-fukuoka\",\"text\":\"hello clipboard relay\"}"
```

然后在 Windows 上粘贴，确认剪贴板内容是 `hello clipboard relay`。

## 后台自启动

```cmd
install_task.cmd
schtasks /Query /TN "ClipboardRelayAgent"
```

该计划任务会在当前用户登录时启动 `.venv\Scripts\pythonw.exe agent.py`。

卸载：

```cmd
uninstall_task.cmd
```

## 日志

日志写入：

```text
%APPDATA%\ClipboardRelay\agent.log
```

日志只记录文本长度，不记录剪贴板内容。
