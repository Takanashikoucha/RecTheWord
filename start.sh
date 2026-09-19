#!/usr/bin/env bash
# RecTheWord v2 启动脚本（Linux）：环境缺失时自动衔接 install.sh
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
if [[ ! -x .venv/bin/python ]]; then
  echo "[start] 未检测到运行环境，正在自动安装..."
  ./install.sh
  if [[ $? -ne 0 ]]; then
    echo "[start] 安装失败，请查看上方日志。"
    exit 1
  fi
fi
exec .venv/bin/python main.py "$@"
