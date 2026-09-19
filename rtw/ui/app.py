"""UI 装配：splash → 主窗口 + 透明浮窗，EventBus 定时泵驱动。

启动序列（用户约束 ⑤⑥）：
  1. splash 显示（入场动画）
  2. 后台线程依次执行 env_check → model_ensure → vad_load → asr_load → api_health
  3. 主线程循环 processEvents + bus.pump，splash 实时跟随进度
  4. 任一阶段 ERROR → splash 显示错误 + 重试/退出按钮
  5. 全部 DONE → 关闭 splash，弹主窗口 + 浮窗
"""
from __future__ import annotations

import logging
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

APP_ROOT = Path(__file__).resolve().parent.parent.parent


def run_app(cfg, bus) -> int:
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from ..core.status_machine import StatusMachine
    from ..core.model_manager import ModelManager
    from ..llm_api.client import LlmApiClient
    from ..pipeline.orchestrator import Pipeline
    from .main_window import MainWindow
    from .overlay import OverlayWindow
    from .splash import SplashWindow

    app = QApplication([])

    # ---- 1. splash ----
    splash = SplashWindow()
    splash.show()
    # 关键：立即订阅 status 事件。否则启动线程发出的进度事件会在 splash 订阅前
    # 就被 pump 掉（竞态），导致进度条卡在 0% 直接闪到主窗口。
    bus.subscribe("status", splash.on_status)
    app.processEvents()

    # ---- 2. 后台启动序列 ----
    startup_result: dict = {}

    def _startup_sequence() -> None:
        """在后台线程中依次执行所有启动阶段，通过 StatusMachine 广播进度。"""
        try:
            # 阶段 1：环境检查（模型文件是否存在 / 从 ModelScope 下载）
            sm_env = StatusMachine(bus, "env_check")
            sm_env.begin("检查模型文件…")
            model_dir = APP_ROOT / "models" / "Qwen3-ASR-0.6B"
            vad_path = APP_ROOT / "models" / "silero_vad" / "silero_vad.onnx"

            mm = ModelManager(cfg.models_dir(), bus)
            if not model_dir.exists() or not any(model_dir.iterdir()):
                mm.ensure("qwen3_asr")
            if not vad_path.exists():
                try:
                    mm.ensure("silero_vad")
                except Exception as e:
                    log.warning("silero_vad 下载失败：%s（VAD 将不可用）", e)

            if not model_dir.exists() or not any(model_dir.iterdir()):
                sm_env.error("ASR 模型文件缺失，请先运行 install.ps1")
                startup_result["error"] = "model_missing"
                return
            sm_env.finish("环境就绪")

            # 阶段 2-4：VAD 加载 + ASR 预热 + API 健康检查
            api = LlmApiClient(cfg.api.base_url, cfg.api.api_key, cfg.api.model)
            pipe = Pipeline(cfg, bus, str(model_dir), api,
                           target_lang=getattr(cfg.ui, "target_lang", "zh"))
            pipe.setup_lanes()
            pipe.warmup()
            pipe.run_lane_workers()

            # 未配置翻译 API → 一次性告知（不逐句报错）
            if api.misconfigured:
                bus.publish("notice", "未配置翻译 API（config.yaml api.base_url），仅显示原文")

            # 阶段 5：就绪（驱动 splash 最后一步 + 进度条满格）
            sm_ready = StatusMachine(bus, "ready")
            sm_ready.begin("所有组件已加载")
            sm_ready.finish("就绪")
            startup_result["pipe"] = pipe
            startup_result["api"] = api
            startup_result["cfg"] = cfg
            startup_result["ok"] = True
        except Exception as e:
            log.exception("启动失败")
            startup_result["error"] = str(e)

    startup_thread = threading.Thread(target=_startup_sequence, daemon=True)
    startup_thread.start()

    # ---- 3. 主线程：循环 pump 直到启动完成或出错 ----
    def _pump_and_check() -> None:
        bus.pump(0.02)
        app.processEvents()
        if "ok" in startup_result or "error" in startup_result:
            check_timer.stop()
            if startup_result.get("ok"):
                _show_main_windows(app, bus, splash, startup_result)
            else:
                splash.show_error(startup_result.get("error", "未知错误"))

    check_timer = QTimer()
    check_timer.timeout.connect(_pump_and_check)
    check_timer.start(33)

    # 如果启动线程在第一次 pump 之前就完成了，立即处理
    app.processEvents()
    if "ok" in startup_result or "error" in startup_result:
        check_timer.stop()
        if startup_result.get("ok"):
            _show_main_windows(app, bus, splash, startup_result)
        else:
            splash.show_error(startup_result.get("error", "未知错误"))

    return app.exec()


def _show_main_windows(app, bus, splash, result: dict) -> None:
    """启动成功后：关闭 splash，弹主窗口 + 浮窗。"""
    from PySide6.QtCore import QTimer
    from .main_window import MainWindow
    from .overlay import OverlayWindow

    pipe = result["pipe"]
    cfg = result["cfg"]

    # 关窗自动收尾（stop 幂等）
    app.aboutToQuit.connect(pipe.stop)

    # 主窗口
    main_win = MainWindow(bus, model_path="",
                         target_lang=getattr(cfg.ui, "target_lang", "zh"),
                         pipeline=pipe)
    main_win.show()

    # 浮窗：启动时默认隐藏（用户要求）。先定位好，但不 show()；
    # 由主窗「— 最小化」或「浮窗」按钮唤起。
    overlay = OverlayWindow()
    overlay.set_theme(getattr(cfg.ui, "overlay_theme", "glass"))
    geo = app.primaryScreen().availableGeometry()
    overlay.move(geo.center().x() - overlay.width() // 2,
                 geo.bottom() - overlay.height() - 80)
    # 不调 overlay.show() → 默认隐藏

    # 浮窗事件 → 主窗口联动
    main_win.attach_overlay(overlay)

    # 主窗「— 最小化」= 切到悬浮界面：主窗最小化（进任务栏，可还原）+ 唤起浮窗。
    # 用 singleShot(0) 把对方 show 推迟到事件循环，避免同帧竞争。
    def _minimize_to_overlay() -> None:
        main_win.showMinimized()
        QTimer.singleShot(0, lambda: (overlay.show(), overlay.activateWindow(), overlay.raise_()))
    main_win.minimize_requested.connect(_minimize_to_overlay)

    # 浮窗「✕ 关闭」= 切回主窗口：浮窗最小化（进任务栏）+ 唤起主窗（不退出进程）
    def _back_to_main() -> None:
        overlay.showMinimized()
        QTimer.singleShot(0, lambda: (main_win.show(), main_win.activateWindow(), main_win.raise_()))
    overlay.hide_requested.connect(_back_to_main)

    # 主窗口 ⇄ 浮窗 互斥切换（点主窗口「隐藏浮窗」→ 只剩浮窗；
    # 点浮窗「—」隐藏 → 只剩主窗口，此时主窗口按钮变「显示浮窗」可唤回）
    def _toggle_overlay() -> None:
        if overlay.isHidden():
            overlay.recenter_bottom(app.primaryScreen().availableGeometry())
            overlay.show()
        else:
            overlay.hide()
        main_win._sync_ov_button()
    main_win.toggle_overlay_requested.connect(_toggle_overlay)

    # 事件桥接：asr/trans → 浮窗（字幕 + 延迟 + 译文回填）
    ov_lines: dict[str, object] = {}

    def on_asr(p) -> None:
        line = overlay.add_line(p["lane"])
        line.set_interim(p["text"])
        ov_lines[p["seg_id"]] = line
        overlay.set_latency(p.get("t_first_ms"))

    def on_trans(p) -> None:
        line = ov_lines.get(p["seg_id"])
        if line:
            line.append_translation(p["delta"])

    def on_trans_err(p) -> None:
        line = ov_lines.get(p["seg_id"])
        if line:
            line.append_translation(f" ⚠{p.get('error', '')}")

    bus.subscribe("asr", on_asr)
    bus.subscribe("trans", on_trans)
    bus.subscribe("trans_err", on_trans_err)

    # 录音状态 → 浮窗状态栏
    overlay.set_idle()

    # 关闭 splash
    splash.close()

    # EventBus 泵
    timer = QTimer()
    timer.timeout.connect(lambda: bus.pump(0.02))
    timer.start(33)

    # 浮窗时钟
    clk = QTimer()
    clk.timeout.connect(overlay.tick_clock)
    clk.start(1000)
