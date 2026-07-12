#!/usr/bin/env bash
# 双击本文件即可安装 / 注册 macOS 剪贴板中继 Agent。
# Finder 双击会自动打开终端运行；共享密码和注册时的设备名提示都可以直接在终端里交互。
set -euo pipefail

# 切到本脚本所在目录（agent/macos），无论从哪里双击都定位正确
cd "$(dirname "${BASH_SOURCE[0]}")"

echo "=== 剪贴板中继 Agent 安装 ==="
echo

# 交给已有的安装脚本完成：装依赖 -> 隐藏输入密码与交互式注册 -> 装并启动后台服务
./scripts/install_launchagent.sh

echo
echo "=== 完成。可以关闭此窗口了。 ==="
# 双击运行时留住窗口，让用户看到结果
read -r -p "按回车键关闭…" _ || true
