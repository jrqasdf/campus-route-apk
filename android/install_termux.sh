#!/data/data/com.termux/files/usr/bin/bash
# 安卓 Termux 一键安装/启动脚本
# 用法：把整个 campus_route_tool 拷到手机（如 ~/campus_route_tool），然后：
#   bash android/install_termux.sh
set -e
cd "$(dirname "$0")/.."

echo "[1/3] 安装依赖（首次可能需要几分钟）…"
pkg update -y >/dev/null 2>&1 || true
pkg install -y python python-pip >/dev/null 2>&1 || true
pip install --upgrade pillow numpy >/dev/null 2>&1 || true

echo "[2/3] 检查依赖…"
python - <<'PY'
import sys
import numpy, PIL
print("python:", sys.version.split()[0])
print("numpy:", numpy.__version__)
print("pillow:", PIL.__version__)
PY

echo "[3/3] 启动本地网页版（用手机浏览器打开 http://127.0.0.1:8765 ）"
echo "提示：长按 Termux 通知里的 URL 可打开浏览器；Ctrl+C 可停止。"
python android/server.py
