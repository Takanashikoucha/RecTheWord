"""UI 冒烟测试（offscreen）：三窗口实例化 + 事件注入 + 截图。

用法：QT_QPA_PLATFORM=offscreen python -m tests.test_ui_smoke
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from rtw.core.config import load_config
from rtw.core.events import EventBus
from rtw.core.status_machine import StatusMachine


def main() -> None:
    app = QApplication([])
    cfg = load_config()
    bus = EventBus()

    from rtw.ui.splash import SplashWindow
    from rtw.ui.main_window import MainWindow
    from rtw.ui.overlay import OverlayWindow

    splash = SplashWindow()
    splash.show()
    main_win = MainWindow(bus, model_path="")
    main_win.show()
    overlay = OverlayWindow()
    overlay.show()
    app.processEvents()

    # 注入状态事件
    sm = StatusMachine(bus, "asr_load")
    sm.begin("ASR 模型加载中…")
    sm.finish("ASR 就绪")
    bus.pump(0.1)

    # 注入字幕事件
    bus.publish("asr", {"lane": "mic", "seg_id": "mic-1", "text": "今天我们来讨论下季度的预算分配。",
                        "language": "Chinese", "t_first_ms": 1200, "t_final_ms": 1200})
    bus.publish("trans", {"lane": "mic", "seg_id": "mic-1", "delta": "Let's discuss the budget."})
    bus.publish("asr", {"lane": "sys", "seg_id": "sys-1", "text": "では、まず予算の確認をさせてください。",
                        "language": "Japanese", "t_first_ms": 900, "t_final_ms": 900})
    bus.pump(0.1)
    app.processEvents()

    # 截图
    out = ROOT / "tests" / "screenshots"
    out.mkdir(exist_ok=True)
    for name, win in (("splash", splash), ("main", main_win), ("overlay", overlay)):
        win.grab().save(str(out / f"{name}.png"))
        print(f"saved {name}.png")

    # 浮窗主题切换
    overlay.set_theme("solid")
    overlay.set_theme("light")
    app.processEvents()
    overlay.grab().save(str(out / "overlay_light.png"))
    print("saved overlay_light.png")

    # 验证主窗口收到了字幕行
    n_lines = sum(1 for i in range(main_win.sub_layout.count())
                 if main_win.sub_layout.itemAt(i).widget()
                 and main_win.sub_layout.itemAt(i).widget().objectName() == "subLine")
    print(f"main window sub lines: {n_lines}")
    assert n_lines >= 2, "主窗口未收到字幕行"
    print("PASS")


if __name__ == "__main__":
    main()
