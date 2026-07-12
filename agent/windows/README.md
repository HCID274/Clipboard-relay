# Windows 剪贴板中继 Agent

该 Agent 在 Windows 用户会话中运行，并通过计划任务保持自启动。日志位于
`%APPDATA%\ClipboardRelay\agent.log`，日志只记录文本长度，不记录剪贴板正文或密码。

## 安装与注册

在资源管理器中双击 `install_task.cmd` 即可完成安装。该脚本会自动创建 `.venv`、安装缺失依赖、复制
`config.example.json`，并通过 PowerShell 的隐藏输入提示用户输入服务端共享密码；用户不需要手动编辑
任何配置文件。

也可以在命令提示符中运行：

```cmd
cd agent\windows
install_task.cmd
```

`password` 只允许使用 ASCII 字符，并且管理员应当配置足够长的随机密码或随机英文词组。
密码已填写且有效时，安装脚本会跳过密码提示，因而可以安全地重复运行。

安装脚本会先执行交互式注册。配置中没有 `device_id` 时，Agent 会显示从 hostname 生成的建议值，
用户可以回车确认或输入新名称。注册成功后，最终 ID 会保存到 `config.json`，计划任务才会创建。
已有 `device_id` 时，Agent 会直接复用该身份。旧 `api_key` 字段仍可读取，但建议迁移为
`password`。密码错误、设备数达到上限和网络错误会显示明确提示。

## 单元测试（务必在本目录执行）

测试文件通过 `import agent` 加载 **`agent/windows/agent.py` 这个单文件模块**。
因此必须先 `cd` 到 `agent\windows`，再运行 pytest。不要从仓库根目录执行
`pytest agent/windows/tests`：那样会把仓库里的 `agent/` 目录当成 Python 包导入，
导致全部测试以 `AttributeError` 失败（找不到 `load_config` 等函数）。

正确写法：

```cmd
cd agent\windows
.venv\Scripts\python.exe -m pytest -q
```

若本机没有 `.venv`，可用任意已安装 `pytest` / `websocket-client` / `pyperclip` 的 Python：

```cmd
cd agent\windows
python -m pytest -q
```

错误写法（从仓库根执行，会导错模块）：

```cmd
python -m pytest agent/windows/tests -q
```

## 前台测试与后台管理

```cmd
.venv\Scripts\python.exe agent.py --register-only
.venv\Scripts\python.exe agent.py
schtasks /Query /TN "ClipboardRelayAgent"
```

连接稳定运行满 60 秒后，Agent 会重置重连计数。卸载命令是 `uninstall_task.cmd`。
