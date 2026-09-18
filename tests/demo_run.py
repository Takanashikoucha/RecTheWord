"""演示运行：完整启动序列 + 真实 pipeline + 关键帧截图。

用法：QT_QPA_PLATFORM=offscreen python -m tests.demo_run

流程：
  1. splash 显示 → 逐阶段加载（截图 splash_loading）
  2. 主窗口 + 浮窗出现（截图 ui_idle）
  3. 真实 pipeline 跑 16x 回放（VAD→ASR→mock 翻译）
  4. 字幕流入时截图（ui_subtitles）
  5. 全部完成后截图（ui_done）+ 输出事件时间线
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from rtw.core.config import load_config
from rtw.core.events import EventBus
from rtw.core.status_machine import StatusMachine
from rtw.llm_api.client import LlmApiClient
from rtw.pipeline.orchestrator import Pipeline
from rtw.ui.main_window import MainWindow
from rtw.ui.overlay import OverlayWindow
from rtw.ui.splash import SplashWindow

SHOTS = ROOT / "tests" / "demo_shots"


def grab(win, name: str) -> None:
    win.grab().save(str(SHOTS / f"{name}.png"))
    print(f"[shot] {name}.png", flush=True)


def main() -> None:
    SHOTS.mkdir(exist_ok=True)
    app = QApplication([])
    cfg = load_config()
    bus = EventBus()
    t0 = time.monotonic()
    timeline: list[tuple[float, str]] = []

    def log(ev: str) -> None:
        timeline.append((round(time.monotonic() - t0, 2), ev))

    # 事件监听（记录时间线）
    bus.subscribe("status", lambda p: log(f"status {p[0]}:{p[2].phase.value} {p[2].message}"))
    bus.subscribe("seg", lambda p: log(f"seg {p['lane']} {p['start_ms']}-{p['end_ms']}ms"))
    bus.subscribe("asr", lambda p: log(f"asr {p['lane']} {p['t_first_ms']}ms {p['text'][:24]}"))
    bus.subscribe("trans", lambda p: log(f"trans {p['seg_id']} +{p['delta'][:12]}"))
    bus.subscribe("trans_err", lambda p: log(f"trans_err {p['error'][:30]}"))

    # pump
    timer = QTimer()
    timer.timeout.connect(lambda: bus.pump(0.02))
    timer.start(33)

    # ---- 1. splash ----
    splash = SplashWindow()
    splash.show()
    app.processEvents()

    sm_env = StatusMachine(bus, "env_check")
    sm_env.begin("检查模型文件…")
    app.processEvents(); time.sleep(0.3)
    grab(splash, "1_splash_env")

    model_dir = ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B/snapshots/master"
    vad_path = ROOT / "models/silero_vad/silero_vad.onnx"
    assert model_dir.exists() and vad_path.exists(), "模型缺失"
    sm_env.finish("环境就绪")
    app.processEvents()

    # ---- 2. pipeline（真实加载 ASR）----
    api_proc = subprocess.Popen(
        [sys.executable, "-m", "tests.mock_api_server", "--port", "8799"],
        cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    api = LlmApiClient("http://127.0.0.1:8799/v1", "sk-demo", "mock-translate-1")
    pipe = Pipeline(cfg, bus, str(model_dir), api, target_lang="zh")
    pipe.setup_lanes()
    pipe.start()  # 内部广播 asr_load / api_health
    pipe.run_lane_workers()
    app.processEvents(); time.sleep(0.5)
    grab(splash, "2_splash_asr_loaded")

    bus.publish("status", ("ready", 0, None))
    app.processEvents()

    # ---- 3. 主窗口 + 浮窗 ----
    main_win = MainWindow(bus, model_path=str(model_dir))
    main_win.show()
    overlay = OverlayWindow()
    overlay.set_theme(cfg.ui.overlay_theme)
    overlay.show()
    app.processEvents(); time.sleep(0.3)
    grab(main_win, "3_ui_idle")

    # 浮窗也画上字幕（桥接）
    ov_lines: dict[str, object] = {}
    def _ov_asr(p):
        ln = overlay.add_line(p["lane"])
        ln.set_interim(p["text"])
        ov_lines[p["seg_id"]] = ln
    bus.subscribe("asr", _ov_asr)
    def _ov_trans(p):
        ln = ov_lines.get(p["seg_id"])
        if ln:
            ln.append_translation(p["delta"])
    bus.subscribe("trans", _ov_trans)

    # ---- 4. 等字幕流入（16x 回放 ~2s + ASR 处理）----
    deadline = time.monotonic() + 40
    last_shot = 0.0
    while time.monotonic() < deadline:
        app.processEvents()
        n = pipe.stats["asr_done"]
        if n >= 2 and time.monotonic() - last_shot > 3:
            grab(main_win, f"4_ui_subtitles_{n}")
            grab(overlay, f"4_overlay_subtitles_{n}")
            last_shot = time.monotonic()
        reps_done = all(l.rep.done.is_set() for l in pipe.lanes if l.rep)
        if reps_done and pipe.stats["trans_done"] >= pipe.stats["asr_done"] and n > 0:
            time.sleep(1.5)
            break
        time.sleep(0.3)

    # ---- 5. 完成态 ----
    grab(main_win, "5_ui_done")
    grab(overlay, "5_overlay_done")
    pipe.stop()
    api_proc.terminate()

    # ---- 输出时间线 ----
    print("\n===== EVENT TIMELINE =====")
    for t, ev in timeline:
        print(f"  {t:6.2f}s  {ev}")
    print(f"\nstats: {pipe.stats}")
    print("DEMO DONE")


if __name__ == "__main__":
    main()
