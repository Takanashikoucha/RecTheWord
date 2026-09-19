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

    cfg = load_config()
    bus = EventBus()

    # 无显示环境（CI/测试）：只做配置加载验证
    if "--headless" in sys.argv:
        log.info("headless boot OK")
        return 0

    # 模型就位检查 + 所有启动阶段均在 app.py 的 splash 流程中完成
    # （失败时 splash 显示友好错误 + 重试/退出按钮，不再直接崩溃）
    from rtw.ui.app import run_app
    return run_app(cfg, bus)


if __name__ == "__main__":
    raise SystemExit(main())
