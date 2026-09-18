"""Splash 启动页：分级懒加载的显式进度告知（用户约束 ⑤⑥）。

阶段序列（每阶段 begin/update/finish 都经 StatusMachine 广播）：
  1. 环境检查（模型文件是否存在）
  2. VAD 模型加载
  3. ASR 模型加载（最久，~2-5s）
  4. 翻译 API 连通性
  5. 就绪
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QLabel, QProgressBar, QVBoxLayout

from ..core.status_machine import Phase, StatusState
from .theme import SPLASH_QSS

log = logging.getLogger(__name__)

TOTAL_STEPS = 5


class SplashWindow(QDialog):
    step_changed = Signal(str, int)  # 文案, 步骤号(1-based)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("RecTheWord")
        self.setFixedSize(460, 300)
        self.setStyleSheet(SPLASH_QSS)
        self._step = 0

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 36, 40, 32)
        root.setSpacing(10)

        logo_box = QLabel("R")
        logo_box.setObjectName("splashLogo")
        logo_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_box.setFixedHeight(64)
        root.addWidget(logo_box)

        title = QLabel("RecTheWord")
        title.setObjectName("splashTitle")
        root.addWidget(title)

        self.step_lbl = QLabel("正在启动…")
        self.step_lbl.setObjectName("splashStep")
        root.addWidget(self.step_lbl)

        self.bar = QProgressBar()
        self.bar.setRange(0, TOTAL_STEPS)
        self.bar.setTextVisible(False)
        root.addWidget(self.bar)

        self.detail_lbl = QLabel("")
        self.detail_lbl.setObjectName("splashDetail")
        root.addWidget(self.detail_lbl)

        root.addStretch(1)
        ver = QLabel("v2.0 · Qwen3-ASR 0.6B · CPU")
        ver.setStyleSheet("color:#5a6578; font-size:11px;")
        root.addWidget(ver, alignment=Qt.AlignmentFlag.AlignRight)

    def set_step(self, n: int, text: str, detail: str = "") -> None:
        self._step = n
        self.step_lbl.setText(text)
        self.detail_lbl.setText(detail)
        self.bar.setValue(min(n, TOTAL_STEPS))
        self.step_changed.emit(text, n)

    def on_status(self, payload) -> None:
        """订阅 EventBus 'status'：把 pipeline 的状态机事件映射到 splash 进度。"""
        op, _seq, st = payload
        mapping = {
            "env_check": (1, "环境检查"),
            "vad_load": (2, "加载语音活动检测"),
            "asr_load": (3, "加载 ASR 模型"),
            "api_health": (4, "检查翻译 API"),
        }
        if op in mapping:
            step, name = mapping[op]
            if st.phase == Phase.WORKING:
                self.set_step(step, f"{name}…", st.message)
            elif st.phase in (Phase.DONE, Phase.ERROR):
                self.set_step(step + 1, "准备就绪" if st.phase == Phase.DONE
                             else f"{name}失败", st.message)
        if op == "ready":
            self.set_step(TOTAL_STEPS, "就绪", st.message or "所有组件已加载")
