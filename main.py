"""RecTheWord v2 入口。

启动顺序（splash 页逐步点亮，等待态显式告知）：
  1. 检查环境/配置
  2. 确保模型就位（ModelScope）
  3. 加载 VAD（立即可用）
  4. 预热 ASR worker（后台，期间字幕区显示骨架）
  5. 健康检查翻译 API
  6. 进入主窗口 / 会议模式浮窗
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rtw.main")


def main() -> int:
    from rtw.core.config import load_config
    from rtw.core.events import EventBus
    from rtw.core.model_manager import ModelManager

    cfg = load_config()
    bus = EventBus()

    # splash 阶段 1：确保模型就位（ModelScope，含 ASR + VAD）
    mm = ModelManager(cfg.models_dir(), bus)
    try:
        mm.ensure("qwen3_asr")
    except Exception as e:  # noqa: BLE001
        log.error("qwen3_asr 模型缺失：%s（请先运行 install.ps1）", e)
        return 1
    try:
        mm.ensure("silero_vad")
    except Exception as e:  # noqa: BLE001
        log.warning("silero_vad 模型缺失：%s（VAD 将不可用）", e)

    # 阶段 3/4/5 在 UI 层（P3）接管后继续；无显示环境（CI/测试）到此为止
    if "--headless" in sys.argv:
        log.info("headless boot OK")
        return 0

    from rtw.ui.app import run_app
    return run_app(cfg, bus)


if __name__ == "__main__":
    raise SystemExit(main())
