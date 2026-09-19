#!/usr/bin/env bash
# RecTheWord v2 安装脚本（Linux；uv 加速；模型只走 ModelScope）
# 用法：./install.sh
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$PROJECT_DIR"

# 系统依赖提示（采集后端所需命令行工具，通常已随发行版预装）
if ! command -v arecord >/dev/null 2>&1; then
  echo "[warn] 未找到 arecord（alsa-utils）。mic 采集需要它。"
  echo "       Debian/Ubuntu: sudo apt-get install alsa-utils"
  echo "       Fedora:       sudo dnf install alsa-utils"
fi
if ! command -v pacat >/dev/null 2>&1 && ! command -v pw-cat >/dev/null 2>&1; then
  echo "[warn] 未找到 pacat / pw-cat。sys 环回（监听扬声器）需要 PulseAudio 或 PipeWire。"
  echo "       Debian/Ubuntu: sudo apt-get install pulseaudio-utils  (或 pipewire)"
fi

echo "==> [1/4] 检查 Python (uv)"
if ! command -v uv >/dev/null 2>&1; then
  echo "    未找到 uv，下载安装..."
  mkdir -p tools
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
  command -v uv >/dev/null 2>&1 || { echo "[error] uv 安装失败"; exit 1; }
fi
echo "    uv OK"

echo "==> [2/4] 创建虚拟环境 .venv (Python 3.12)"
uv venv --python 3.12 .venv

echo "==> [3/4] 安装依赖 (uv pip，比 pip 快约 10 倍)"
uv pip install --python .venv/bin/python -r requirements.txt
uv pip install --python .venv/bin/python torch --index-url https://download.pytorch.org/whl/cpu

echo "==> [4/4] 预取模型 (ModelScope)"
mkdir -p models
.venv/bin/python -c "import sys; sys.path.insert(0,'.'); from rtw.core.model_manager import ModelManager; from rtw.core.events import EventBus; mm=ModelManager(__import__('pathlib').Path('models'), EventBus()); [mm.ensure(k) for k in ['qwen3_asr','silero_vad']]; print('models ready')"

echo ""
echo "安装完成。运行 ./start.sh 启动。"
