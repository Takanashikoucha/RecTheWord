"""UI 装配：splash → 主窗口 + 透明浮窗，EventBus 定时泵驱动。

启动序列（用户约束 ⑤⑥）：
  1. splash 显示
  2. 环境检查（模型文件）→ StatusMachine("env_check")
  3. Pipeline 启动（VAD/ASR 加载、API 健康检查，各自广播状态）
  4. splash 关闭 → 主窗口 + 浮窗出现
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent.parent.parent


def run_app(cfg, bus) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ..core.status_machine import StatusMachine
    from ..llm_api.client import LlmApiClient
    from ..pipeline.orchestrator import Pipeline
    from .main_window import MainWindow
    from .overlay import OverlayWindow
    from .splash import SplashWindow

    app = QApplication([])

    # ---- 1. splash ----
    splash = SplashWindow()
    splash.show()
    app.processEvents()

    # ---- 2. 环境检查 ----
    sm_env = StatusMachine(bus, "env_check")
    sm_env.begin("检查模型文件…")
    app.processEvents()
    model_dir = APP_ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B/snapshots/master"
    vad_path = APP_ROOT / "models/silero_vad/silero_vad.onnx"
    if not model_dir.exists() or not vad_path.exists():
        sm_env.error("模型文件缺失，请先运行 install.ps1")
        app.processEvents()
        return 1
    sm_env.finish("环境就绪")
    app.processEvents()

    # ---- 3. pipeline 启动（内部广播 asr_load / api_health 状态）----
    api = LlmApiClient(cfg.api.base_url, cfg.api.api_key, cfg.api.model) \
        if cfg.api.base_url else LlmApiClient("", "", "")
    pipe = Pipeline(cfg, bus, str(model_dir), api,
                   target_lang=getattr(cfg.ui, "target_lang", "zh"))
    pipe.setup_lanes()
    pipe.start()
    pipe.run_lane_workers()
    bus.publish("status", ("ready", 0, None))
    app.processEvents()

    # ---- 4. 主窗口 + 浮窗 ----
    main_win = MainWindow(bus)
    main_win.show()

    overlay = OverlayWindow()
    overlay.set_theme(cfg.ui.overlay_theme)
    overlay.show()
    # 居中偏下
    geo = app.primaryScreen().availableGeometry()
    overlay.move(geo.center().x() - overlay.width() // 2,
                 geo.bottom() - overlay.height() - 80)

    # 浮窗事件 → 主窗口联动
    overlay.hide_requested.connect(main_win.close)

    # 事件桥接：asr/trans → 浮窗
    from PySide6.QtCore import QObject, Signal
    bridge = QObject()

    def on_asr(p) -> None:
        line = overlay.add_line(p["lane"])
        line.set_interim(p["text"])
        main_win._line_refs[p["seg_id"]] = None  # 主窗口自己也会画

    def on_trans(p) -> None:
        # 找到浮窗最后一条对应行的译文追加
        pass  # 浮窗译文回填在 P4 完善（主窗口已实时）

    bus.subscribe("asr", on_asr)
    bus.subscribe("trans", on_trans)

    # ---- EventBus 泵 ----
    timer = QTimer()
    timer.timeout.connect(lambda: bus.pump(0.02))
    timer.start(33)

    # 浮窗时钟
    clk = QTimer()
    clk.timeout.connect(overlay.tick_clock)
    clk.start(1000)

    return app.exec()
