"""全量 E2E 视频捕获：真实 pipeline（TTS 双通道）+ 真实 GUI，全程截帧合成 MP4。

画面布局：上 = 主窗口（1280x760），下 = 透明浮窗（980x220，贴桌面背景色模拟半透明）。
时长：83s 音频 @2x ≈ 42s + 启动 ~5s ≈ 47s 视频。

用法：QT_QPA_PLATFORM=offscreen python -m tests.demo_video
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
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtWidgets import QApplication

from rtw.core.config import load_config
from rtw.core.events import EventBus
from rtw.llm_api.client import LlmApiClient
from rtw.pipeline.orchestrator import Pipeline
from rtw.ui.main_window import MainWindow
from rtw.ui.overlay import OverlayWindow
from rtw.ui.splash import SplashWindow

FRAMES = ROOT / "tests" / "video_frames"
OUT_MP4 = ROOT / "tests" / "e2e_demo.mp4"
SPEED = 2.0


def _to_qimage(pic) -> QImage:
    """QPixmap/QWidget grab() 结果 → QImage。"""
    if isinstance(pic, QImage):
        return pic
    return pic.toImage()


def composite(main_pic, ov_pic, desktop_bg: str) -> QImage:
    """主窗口在上，浮窗贴在模拟桌面背景上（下半部）。接受 QPixmap 或 QImage。"""
    main_img = _to_qimage(main_pic)
    ov_img = _to_qimage(ov_pic)
    W = max(main_img.width(), ov_img.width() + 40)
    H = main_img.height() + ov_img.height() + 30
    canvas = QImage(W, H, QImage.Format.Format_ARGB32)
    canvas.fill(QColor(desktop_bg))
    from PySide6.QtCore import QRect
    p = QPainter(canvas)
    p.drawImage(QRect(0, 0, main_img.width(), main_img.height()), main_img)
    p.drawImage(QRect(20, main_img.height() + 20, ov_img.width(), ov_img.height()), ov_img)
    p.end()
    return canvas


def main() -> None:
    FRAMES.mkdir(exist_ok=True)
    for f in FRAMES.glob("*.png"):
        f.unlink()
    app = QApplication([])
    cfg = load_config()
    cfg.audio.source = "replay"
    cfg.audio.replay_mic = "tests/fixtures/meeting_tri/mic_tri.wav"
    cfg.audio.replay_sys = "tests/fixtures/meeting_tri/sys_tri.wav"
    cfg.audio.replay_speed = SPEED
    bus = EventBus()

    proc = subprocess.Popen([sys.executable, "-m", "tests.mock_api_server", "--port", "8807"],
                           cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    time.sleep(1.0)
    api = LlmApiClient("http://127.0.0.1:8807/v1", "sk", "mock")
    model_dir = ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B"

    # splash 阶段（截 2 帧）
    splash = SplashWindow()
    splash.show()
    app.processEvents()
    pipe = Pipeline(cfg, bus, str(model_dir), api, target_lang="zh",
                   session_base=str(ROOT / "tmp" / "sess_video"))
    pipe.start_session({"case": "video_demo"})
    pipe.setup_lanes()
    t0 = time.monotonic()
    pipe.start()
    app.processEvents()
    time.sleep(0.8)
    composite(splash.grab(), splash.grab(), "#1a2233").save(str(FRAMES / f"f{int(time.time()-t0)*10:05d}.png"))

    # 主窗口 + 浮窗
    main_win = MainWindow(bus, model_path=str(model_dir))
    main_win.show()
    overlay = OverlayWindow()
    overlay.set_theme(cfg.ui.overlay_theme)
    overlay.show()
    app.processEvents()
    pipe.run_lane_workers()

    # 浮窗字幕桥接（字幕 + 延迟 + 状态，与 app.py 生产路径一致）
    ov_lines: dict[str, object] = {}
    def _ov_asr(p):
        ln = overlay.add_line(p["lane"])
        ln.set_interim(p["text"])
        ov_lines[p["seg_id"]] = ln
        overlay.set_latency(p.get("t_first_ms"))
    def _ov_trans(p):
        ln = ov_lines.get(p["seg_id"])
        if ln:
            ln.append_translation(p["delta"])
    bus.subscribe("asr", _ov_asr)
    bus.subscribe("trans", _ov_trans)
    overlay.set_running(True)

    # 事件泵 + 时钟
    timer = QTimer()
    timer.timeout.connect(lambda: bus.pump(0.02))
    timer.start(33)
    clk = QTimer()
    clk.timeout.connect(overlay.tick_clock)
    clk.start(1000)

    # 全程截帧（每 ~0.4s 一帧）
    deadline = time.monotonic() + 202 / SPEED + 30
    last = 0.0
    n = 0
    while time.monotonic() < deadline:
        app.processEvents()
        now = time.monotonic() - t0
        if now - last >= 0.4:
            img = composite(main_win.grab(), overlay.grab(), "#232a3d")
            img.save(str(FRAMES / f"f{n:05d}.png"))
            n += 1
            last = now
        reps_done = all(l.rep.done.is_set() for l in pipe.lanes if l.rep)
        if reps_done and all(l.seg_q.empty() for l in pipe.lanes) and pipe.stats["asr_done"] > 10:
            time.sleep(2.0)
            break
        time.sleep(0.05)

    # 末帧
    composite(main_win.grab(), overlay.grab(), "#232a3d").save(str(FRAMES / f"f{n:05d}.png"))
    n += 1
    pipe.stop()
    proc.terminate()

    # 合成 MP4（2.5fps 截帧 → 25fps 平滑）
    subprocess.run([
        "ffmpeg", "-y", "-framerate", "2.5", "-i", str(FRAMES / "f%05d.png"),
        "-vf", "scale=1280:-2,fps=25", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "20", str(OUT_MP4)], check=True, capture_output=True)
    print(f"stats: {pipe.stats}")
    print(f"frames: {n}")
    print(f"video: {OUT_MP4} ({OUT_MP4.stat().st_size/1024/1024:.1f} MB)")


if __name__ == "__main__":
    main()
