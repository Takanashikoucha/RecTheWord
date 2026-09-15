"""Main application window.

Wires together device selection, capture, the dual streaming pipeline, the two
real-time ASR engines, the (optional) translator, and the stop-time offline
diarization + speaker labeling + meeting minutes.
"""

from __future__ import annotations

import datetime as _dt
import logging
import threading
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QLineEdit, QMainWindow, QMessageBox, QPlainTextEdit, QPushButton,
    QScrollArea, QSplitter, QStatusBar, QTextEdit, QToolBar, QVBoxLayout,
    QWidget,
)

from .. import APP_NAME
from ..ai.minutes import generate_minutes
from ..ai.translate import Translator
from ..asr.engine import StreamingEngine
from ..audio.capture import CHUNK_SAMPLES, CaptureConfig, CaptureEngine
from ..audio.devices import find_ffmpeg, list_audio_devices, list_render_devices
from ..config import AppConfig, recordings_dir
from ..labels import SpeakerLabels
from ..pipeline.diarize import diarize_wav
from ..pipeline.dual_pipeline import DualAudioPipeline
from .settings_dialog import SettingsDialog

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Worker threads
# ---------------------------------------------------------------------------

class ModelLoadWorker(QThread):
    """Loads the two streaming engines (+ optional offline) off the UI thread."""
    done = Signal()
    error = Signal(str)

    def __init__(self, settings, need_offline: bool, parent=None):
        super().__init__(parent)
        self._settings = settings
        self._need_offline = need_offline
        self.engines: dict = {}

    def run(self):
        try:
            mic = StreamingEngine(self._settings)
            spk = StreamingEngine(self._settings)
            mic.load(self._progress)
            self.engines = {"mic": mic, "spk": spk}
            if self._need_offline:
                from ..asr.engine import OfflineEngine
                off = OfflineEngine(self._settings)
                off.load(self._progress)
                self.engines["offline"] = off
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
            return
        self.done.emit()

    def _progress(self, msg: str):
        log.info(msg)


class StopWorker(QThread):
    """Runs offline diarization + (optional) minutes after stop, off-thread."""
    progress = Signal(str)
    result = Signal(object, object)  # (segments, labels)
    minutes = Signal(str)
    error = Signal(str)

    def __init__(self, wav_path: str, settings, ai_enabled: bool, parent=None):
        super().__init__(parent)
        self._wav = wav_path
        self._settings = settings
        self._ai_enabled = ai_enabled

    def run(self):
        try:
            labels = SpeakerLabels(mapping=self._settings.speaker_labels,
                                   default_prefix=(
                                       self._settings.default_speaker_names[0]
                                       if self._settings.default_speaker_names
                                       else "说话人"))
            segments = diarize_wav(self._wav, self._settings,
                                   progress_cb=self.progress.emit)
        except Exception as exc:  # noqa: BLE001
            self.error.emit(str(exc))
            return
        self.result.emit(segments, labels)
        if self._ai_enabled:
            try:
                minutes = generate_minutes(self._settings, segments, labels)
                self.minutes.emit(minutes)
            except Exception as exc:  # noqa: BLE001
                self.minutes.emit(f"（会议纪要生成失败：{exc}）")


# ---------------------------------------------------------------------------
# Main window
# ---------------------------------------------------------------------------

class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self.setWindowTitle(f"{APP_NAME} — 实时多语转录 · 说话人分离 · 会议纪要")
        self.resize(1200, 800)

        # ---- runtime state
        self._capture = CaptureEngine()
        self._pipeline: Optional[DualAudioPipeline] = None
        self._load_worker: Optional[ModelLoadWorker] = None
        self._stop_worker: Optional[StopWorker] = None
        self._stream_engines: dict = {}
        self._translators: dict = {}
        self._current_wav: str = ""
        self._labels: Optional[SpeakerLabels] = None
        self._segments: List = []
        self._state = "idle"  # idle | loading | recording | finishing

        self._build_ui()
        self._refresh_devices()
        self._set_status("就绪")

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)

        # ---- top control bar
        top = QHBoxLayout()
        self.cmb_mic = QComboBox()
        self.cmb_spk = QComboBox()
        self.btn_refresh = QPushButton("刷新设备")
        self.btn_refresh.clicked.connect(self._refresh_devices)
        self.btn_start = QPushButton("●  开始")
        self.btn_start.setObjectName("startBtn")
        self.btn_start.clicked.connect(self._on_start)
        self.btn_stop = QPushButton("■  停止")
        self.btn_stop.setEnabled(False)
        self.btn_stop.clicked.connect(self._on_stop)
        self.btn_settings = QPushButton("⚙ 设置")
        self.btn_settings.clicked.connect(self._on_settings)

        top.addWidget(QLabel("麦克风"))
        top.addWidget(self.cmb_mic, 2)
        top.addWidget(QLabel("扬声器（回环）"))
        top.addWidget(self.cmb_spk, 2)
        top.addWidget(self.btn_refresh)
        top.addStretch(1)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_stop)
        top.addWidget(self.btn_settings)
        root.addLayout(top)

        # ---- live captions + translation (splitter)
        splitter = QSplitter(Qt.Horizontal)

        # live captions group
        live_box = QGroupBox("实时字幕（分路：🎤 麦克风 / 🔊 扬声器）")
        live_lay = QVBoxLayout(live_box)
        self.txt_mic = QTextEdit()
        self.txt_mic.setReadOnly(True)
        self.txt_mic.setPlaceholderText("麦克风的实时字幕将显示在这里…")
        self.txt_spk = QTextEdit()
        self.txt_spk.setReadOnly(True)
        self.txt_spk.setPlaceholderText("扬声器（回环）的实时字幕将显示在这里…")
        live_lay.addWidget(QLabel("🎤 麦克风"))
        live_lay.addWidget(self.txt_mic, 1)
        live_lay.addWidget(QLabel("🔊 扬声器"))
        live_lay.addWidget(self.txt_spk, 1)
        splitter.addWidget(live_box)

        # translation group
        tr_box = QGroupBox("实时中文翻译")
        tr_lay = QVBoxLayout(tr_box)
        self.txt_tr_mic = QTextEdit()
        self.txt_tr_mic.setReadOnly(True)
        self.txt_tr_mic.setPlaceholderText("（未启用翻译或未配置 AI）")
        self.txt_tr_spk = QTextEdit()
        self.txt_tr_spk.setReadOnly(True)
        self.txt_tr_spk.setPlaceholderText("（未启用翻译或未配置 AI）")
        tr_lay.addWidget(QLabel("🎤 麦克风 译文"))
        tr_lay.addWidget(self.txt_tr_mic, 1)
        tr_lay.addWidget(QLabel("🔊 扬声器 译文"))
        tr_lay.addWidget(self.txt_tr_spk, 1)
        splitter.addWidget(tr_box)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        root.addWidget(splitter, 3)

        # ---- offline results: labeled transcript + minutes (splitter)
        off_splitter = QSplitter(Qt.Horizontal)

        tr_box2 = QGroupBox("说话人全量稿（结束后可改名）")
        tr_lay2 = QVBoxLayout(tr_box2)
        self.txt_transcript = QTextEdit()
        self.txt_transcript.setReadOnly(True)
        self.txt_transcript.setPlaceholderText("结束录制后，将显示带说话人与时间戳的全量稿。")
        tr_lay2.addWidget(self.txt_transcript, 1)
        off_splitter.addWidget(tr_box2)

        mn_box = QGroupBox("会议纪要")
        mn_lay = QVBoxLayout(mn_box)
        self.txt_minutes = QTextEdit()
        self.txt_minutes.setReadOnly(True)
        self.txt_minutes.setPlaceholderText("结束录制后，若已配置 AI，将在此生成会议纪要。")
        mn_lay.addWidget(self.txt_minutes, 1)
        off_splitter.addWidget(mn_box)

        root.addWidget(off_splitter, 2)

        # ---- status bar
        self._status = QStatusBar()
        self.setStatusBar(self._status)

        # ---- stylesheet
        self.setStyleSheet(
            "QPushButton#startBtn { font-weight: bold; color: #1d6f3c; }"
            "QTextEdit { font-size: 14px; }"
            "QGroupBox { font-weight: bold; }"
        )

    # -------------------------------------------------------------- status
    def _set_status(self, msg: str) -> None:
        self._status.showMessage(msg, 8000)

    # ----------------------------------------------------------- devices
    def _refresh_devices(self) -> None:
        self._set_status("正在枚举音频设备…")
        mic, spk = _run_device_listing()
        self._populate(self.cmb_mic, mic)
        self._populate(self.cmb_spk, spk)
        self._restore_device(self.cmb_mic, self._config.mic_device, mic)
        self._restore_device(self.cmb_spk, self._config.spk_device, spk)
        if not mic:
            self._set_status("未找到麦克风（请确认已安装 ffmpeg 且在 Windows 上）")
        else:
            self._set_status(f"已发现 {len(mic)} 个麦克风 / {len(spk)} 个扬声器")

    def _populate(self, combo: QComboBox, names: List[str]) -> None:
        combo.blockSignals(True)
        combo.clear()
        for n in names:
            combo.addItem(n)
        combo.blockSignals(False)

    def _restore_device(self, combo: QComboBox, value: str, names: List[str]) -> None:
        if value in names:
            combo.setCurrentText(value)

    # ------------------------------------------------------------- start
    def _on_start(self) -> None:
        if self._state != "idle":
            return
        mic = self.cmb_mic.currentText()
        spk = self.cmb_spk.currentText()
        if not mic:
            QMessageBox.warning(self, APP_NAME, "请先选择麦克风。")
            return
        if find_ffmpeg() is None:
            QMessageBox.critical(
                self, APP_NAME,
                "未找到 ffmpeg。请运行 scripts/download_ffmpeg_windows.py 或安装系统 ffmpeg。",
            )
            return

        self._config.mic_device = mic
        self._config.spk_device = spk
        self._save_config()

        # prepare session wav
        ts = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        self._current_wav = str(recordings_dir() / f"session-{ts}.wav")

        # reset transcript / minutes
        self.txt_transcript.clear()
        self.txt_minutes.clear()
        self.txt_mic.clear()
        self.txt_spk.clear()
        self.txt_tr_mic.clear()
        self.txt_tr_spk.clear()
        self._segments = []
        self._labels = SpeakerLabels(
            mapping=self._config.speaker_labels,
            default_prefix=(self._config.default_speaker_names[0]
                            if self._config.default_speaker_names else "说话人"),
        )

        need_offline = True  # we always want stop-time diarization
        self._state = "loading"
        self._set_status("正在加载 ASR 模型（首次运行会下载模型，请耐心等待）…")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)

        self._load_worker = ModelLoadWorker(self._config.asr, need_offline, self)
        self._load_worker.done.connect(self._on_models_loaded)
        self._load_worker.error.connect(self._on_model_error)
        self._load_worker.start()

    def _on_models_loaded(self) -> None:
        self._stream_engines = {
            "mic": self._load_worker.engines["mic"],
            "spk": self._load_worker.engines["spk"],
        }
        # translators
        ai_enabled = self._config.ai.configured
        self._translators = {
            "mic": Translator(self._config.ai,
                              lambda t: self._queue_translation("mic", t),
                              enabled=ai_enabled),
            "spk": Translator(self._config.ai,
                              lambda t: self._queue_translation("spk", t),
                              enabled=ai_enabled),
        }
        self._start_capture()

    def _start_capture(self) -> None:
        cfg = CaptureConfig(
            mic_device=self.cmb_mic.currentText(),
            spk_device=self.cmb_spk.currentText(),
            out_wav=self._current_wav,
            enable_loopback=bool(self.cmb_spk.currentText()),
        )
        try:
            self._capture.start(cfg)
        except Exception as exc:  # noqa: BLE001
            self._state = "idle"
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self._set_status(f"启动采集失败：{exc}")
            QMessageBox.critical(self, APP_NAME, f"启动采集失败：\n{exc}")
            return

        self._pipeline = DualAudioPipeline(
            self._capture, enable_loopback=cfg.enable_loopback, parent=self)
        self._pipeline.chunk.connect(self._on_chunk)
        self._pipeline.finished.connect(self._on_pipeline_finished)
        self._pipeline.error.connect(self._on_pipeline_error)
        self._pipeline.start()

        self._state = "recording"
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._set_status("录制中…（实时字幕与翻译开始）")

    # --------------------------------------------------------------- chunks
    def _on_chunk(self, channel: str, samples, offset_s: float) -> None:
        eng = self._stream_engines.get(channel)
        if eng is None:
            return
        try:
            text = eng.feed(samples)
        except Exception as exc:  # noqa: BLE001
            self._set_status(f"ASR 错误：{exc}")
            return
        if not text:
            return
        # append to the correct live caption box
        box = self.txt_mic if channel == "mic" else self.txt_spk
        self._append_caption(box, text, offset_s)
        # feed translator
        tr = self._translators.get(channel)
        if tr is not None and tr.enabled:
            tr.feed(text)

    def _append_caption(self, box: QTextEdit, text: str, offset_s: float) -> None:
        stamp = _fmt_clock(offset_s)
        # move to end and append
        cursor = box.textCursor()
        cursor.movePosition(QTextCursor.End)
        # a new "sentence" starts a new line
        if not box.toPlainText().strip():
            cursor.insertText(f"[{stamp}] {text}\n")
        else:
            cursor.insertText(f"{text}\n")
        box.ensureCursorVisible()

    def _queue_translation(self, channel: str, text: str) -> None:
        box = self.txt_tr_mic if channel == "mic" else self.txt_tr_spk
        box.append(text)
        box.ensureCursorVisible()

    # -------------------------------------------------------------- stop
    def _on_stop(self) -> None:
        if self._state != "recording":
            return
        self._state = "finishing"
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self._set_status("正在停止采集并冲刷识别缓存…")
        # flush translators
        for tr in self._translators.values():
            tr.flush()
        # stop capture; pipeline detects EOF and finishes
        self._capture.stop()
        # finalize both streaming engines
        for channel, eng in self._stream_engines.items():
            try:
                final = eng.finalize()
            except Exception as exc:  # noqa: BLE001
                self._set_status(f"冲刷错误：{exc}")
                continue
            if final:
                box = self.txt_mic if channel == "mic" else self.txt_spk
                self._append_caption(box, final, 0.0)

    def _on_pipeline_finished(self) -> None:
        # capture ended (or was stopped). Run offline diarization.
        if self._state == "idle":
            return
        self._state = "finishing"
        self._set_status("正在离线做说话人分离（可能需要数十秒到数分钟）…")
        self._stop_worker = StopWorker(self._current_wav, self._config.asr,
                                       ai_enabled=self._config.ai.configured,
                                       parent=self)
        self._stop_worker.progress.connect(self._set_status)
        self._stop_worker.result.connect(self._on_diarize_result)
        self._stop_worker.minutes.connect(self._on_minutes)
        self._stop_worker.error.connect(self._on_diarize_error)
        self._stop_worker.start()

    def _on_pipeline_error(self, err: str) -> None:
        self._set_status(f"采集错误：{err}")

    def _on_diarize_result(self, segments, labels: SpeakerLabels) -> None:
        self._segments = segments
        self._labels = labels
        text = labels.to_transcript_text(segments)
        self.txt_transcript.setPlainText(text or "（未识别到内容）")
        self._state = "idle"
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_status(
            f"完成：共 {len(segments)} 句，{len(labels.distinct_speakers(segments))} 位说话人。"
            "可在全量稿中点击说话人改名。"
        )

    def _on_minutes(self, minutes: str) -> None:
        self.txt_minutes.setPlainText(minutes)

    def _on_diarize_error(self, err: str) -> None:
        self._state = "idle"
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_status(f"离线分离失败：{err}")
        QMessageBox.warning(self, APP_NAME, f"离线说话人分离失败：\n{err}")

    # ------------------------------------------------------- model loading
    def _on_model_error(self, err: str) -> None:
        self._state = "idle"
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_status(f"模型加载失败：{err}")
        QMessageBox.critical(self, APP_NAME, f"模型加载失败：\n{err}")

    # ------------------------------------------------------------- settings
    def _on_settings(self) -> None:
        dlg = SettingsDialog(self._config, self)
        if dlg.exec():
            self._config = dlg.apply()
            self._save_config()
            self._set_status("设置已保存")

    def _save_config(self) -> None:
        try:
            self._config.save()
        except OSError as exc:
            log.warning("config save failed: %s", exc)

    # --------------------------------------------------------------- close
    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            if self._state == "recording":
                self._capture.stop()
            if self._pipeline and self._pipeline.isRunning():
                self._pipeline.request_stop()
                self._pipeline.wait(2000)
            if self._load_worker and self._load_worker.isRunning():
                self._load_worker.wait(2000)
            if self._stop_worker and self._stop_worker.isRunning():
                self._stop_worker.wait(2000)
        finally:
            self._save_config()
            event.accept()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _fmt_clock(seconds: float) -> str:
    total = int(seconds)
    m, s = divmod(total, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def _run_device_listing():
    """Run device listing on a worker thread, returning (mic, spk) lists."""
    import queue as _queue

    q: _queue.Queue = _queue.Queue()

    def work():
        try:
            devs = list_audio_devices()
            mics = list(devs.microphones)
            spks = list_render_devices() or list(devs.speakers)
            q.put((mics, spks))
        except Exception as exc:  # noqa: BLE001
            q.put(([], []))
            log.warning("device listing failed: %s", exc)

    t = threading.Thread(target=work, daemon=True)
    t.start()
    try:
        return q.get(timeout=30)
    except _queue.Empty:
        return ([], [])


def create_main_window(config: AppConfig) -> MainWindow:
    return MainWindow(config)
