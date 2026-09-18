"""GUI 全状态/功能截图矩阵（发行级质感的视觉证据）。

覆盖：
  1. splash 启动进度
  2. 主窗口：录音中（实时字幕 + 译文 + 延迟 + 说话人/磁盘真实值）
  3. 主窗口：已暂停（顶栏黄色 pill）
  4. 主窗口：已停止（顶栏灰色 pill + 按钮禁用）
  5. 浮窗：玻璃主题（上下两分区 + 延迟 + 状态）
  6. 浮窗：浅色主题
  7. 浮窗：暗色/霓虹主题
  8. 设备下拉（系统默认 + 模拟设备）
  9. 导出记录 toast
  10. 生成会议纪要 toast
每张截图存 tests/screenshots/，文件名即状态名。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
SHOT = ROOT / "tests" / "screenshots"
if SHOT.exists():
    shutil.rmtree(SHOT)
SHOT.mkdir(parents=True)

from PySide6.QtWidgets import QApplication
app = QApplication.instance() or QApplication([])

from rtw.core.config import load_config
from rtw.core.events import EventBus
from rtw.llm_api.client import LlmApiClient
from rtw.pipeline.orchestrator import Pipeline
from rtw.ui.main_window import MainWindow
from rtw.ui.overlay import OverlayWindow

cfg = load_config()
cfg.audio.source = "replay"
cfg.audio.replay_mic = "tests/fixtures/meeting_tri/mic_tri.wav"
cfg.audio.replay_sys = "tests/fixtures/meeting_tri/sys_tri.wav"
bus = EventBus()
proc = subprocess.Popen([sys.executable, "-m", "tests.mock_api_server", "--port", "8822"],
                       cwd=str(ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
time.sleep(1.0)
api = LlmApiClient("http://127.0.0.1:8822/v1", "sk", "mock")
pipe = Pipeline(cfg, bus, str(ROOT / ".modelscope_cache/models/Qwen--Qwen3-ASR-0.6B"),
               api, target_lang="zh", session_base=str(ROOT / "tmp" / "sess_shot"))
pipe.start_session({"case": "gui_matrix"})
pipe.setup_lanes()
win = MainWindow(bus, model_path="", pipeline=pipe)
ov = OverlayWindow()
ov.set_theme("glass")
# 浮窗桥接
ov_lines: dict[str, object] = {}
def _ov_asr(p):
    ln = ov.add_line(p["lane"]); ln.set_interim(p["text"]); ov_lines[p["seg_id"]] = ln
    ov.set_latency(p.get("t_first_ms"))
def _ov_trans(p):
    ln = ov_lines.get(p["seg_id"])
    if ln: ln.append_translation(p["delta"])
bus.subscribe("asr", _ov_asr); bus.subscribe("trans", _ov_trans)
ov.set_running(True)

def shot(name: str, widget) -> None:
    widget.show(); app.processEvents()
    widget.grab().save(str(SHOT / f"{name}.png"))
    print(f"  📸 {name}.png")

def pump(ms: float) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        bus.pump(0.02); app.processEvents(); time.sleep(0.01)

# 启动 pipeline 喂数据
pipe.start()
pipe.run_lane_workers()

print("生成 GUI 截图矩阵…")
# 1. 录音中（等足 ASR 模型加载 + 几段字幕出来；replay 1x 需足够墙钟时间）
pump(45000)
shot("01_recording", win)
# 2. 暂停
win._on_pause_toggled(True); app.processEvents()
shot("02_paused", win)
# 3. 停止
win._on_stop_clicked(); app.processEvents()
shot("03_stopped", win)
# 4-7. 浮窗四主题
for theme in ("glass", "dark", "light", "neon"):
    ov.set_theme(theme); app.processEvents()
    shot(f"04_overlay_{theme}", ov)
# 8. 设备下拉（模拟设备填充）
win.lane_mic.set_devices([{"index": 1, "name": "USB 麦克风"},
                          {"index": 2, "name": "Blue Yeti"}])
win.lane_mic.device.setCurrentIndex(1); app.processEvents()
shot("05_device_dropdown", win.lane_mic)
# 9. 导出记录 toast
win._on_export(); app.processEvents()
shot("06_export_toast", win)
# 10. 生成会议纪要 toast
win._on_generate_minutes(); app.processEvents()
shot("07_minutes_toast", win)

pipe.stop()
proc.terminate()
print(f"\n截图已保存到 {SHOT}/（共 10 张）")
