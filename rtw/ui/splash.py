"""Splash 启动页：分级懒加载的显式进度告知（用户约束 ⑤⑥）。

阶段序列（每阶段 begin/update/finish 都经 StatusMachine 广播）：
  1. 环境检查（模型文件是否存在 / ModelScope 下载）
  2. VAD 模型加载
  3. ASR 模型加载（最久，~2-5s）
  4. 翻译 API 连通性
  5. 就绪

视觉：入场 fade-in 动画 + 步骤指示器（✓/●/○）+ 进度条微光 + 错误态按钮。
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Property, QPropertyAnimation, QEasingCurve, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QLinearGradient, QBrush, QPen
from PySide6.QtWidgets import (QDialog, QLabel, QProgressBar, QVBoxLayout,
                               QHBoxLayout, QPushButton, QWidget, QGraphicsDropShadowEffect)

from ..core.status_machine import Phase, StatusState
from .theme import SPLASH_QSS

log = logging.getLogger(__name__)

TOTAL_STEPS = 5

# 步骤定义：(op_name, 显示名, 图标)
STEPS = [
    ("env_check", "环境检查", "[ENV]"),
    ("vad_load", "语音活动检测", "[VAD]"),
    ("asr_load", "ASR 模型加载", "[ASR]"),
    ("api_health", "翻译 API 检查", "[API]"),
    ("ready", "就绪", "✓"),
]


class StepIndicator(QLabel):
    """单个步骤指示器：图标 + 状态点 + 文字。"""

    def __init__(self, icon: str, text: str, parent=None) -> None:
        super().__init__(parent)
        self._icon = icon
        self._text = text
        self._state = "pending"  # pending | active | done | error
        self.setObjectName("stepRow")
        self.setMinimumHeight(32)
        self._update_display()

    def set_state(self, state: str) -> None:
        self._state = state
        self._update_display()

    def _update_display(self) -> None:
        if self._state == "done":
            dot = "✓"
            style = "color:#2a8646;"
        elif self._state == "active":
            dot = "●"
            style = "color:#008390;"
        elif self._state == "error":
            dot = "✗"
            style = "color:#c0392b;"
        else:
            dot = "○"
            style = "color:#9a8f82;"
        txt_color = "#251c15" if self._state in ("active", "done") else "#64584f"
        self.setText(f'<span style="{style}font-size:14px;">{dot}</span>'
                     f'  <span style="font-size:13px;color:{txt_color};">{self._icon} {self._text}</span>')
        self.setTextFormat(Qt.TextFormat.RichText)


class SplashWindow(QDialog):
    """启动页：入场动画 + 实时步骤指示 + 进度条 + 错误态。"""

    step_changed = Signal(str, int)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("RecTheWord")
        self.setFixedSize(480, 420)
        self.setStyleSheet(SPLASH_QSS)
        self._step = 0
        self._opacity = 0.0
        self._error_shown = False

        # 入场动画
        self.setWindowOpacity(0.0)
        anim = QPropertyAnimation(self, b"windowOpacity")
        anim.setDuration(400)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        anim.start()

        root = QVBoxLayout(self)
        root.setContentsMargins(40, 32, 40, 28)
        root.setSpacing(6)

        # Logo（渐变圆角方块 + 品牌名）
        logo_row = QHBoxLayout()
        logo_row.setSpacing(12)
        logo_box = QLabel("RW")
        logo_box.setObjectName("splashLogo")
        logo_box.setFixedSize(48, 48)
        logo_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        logo_row.addWidget(logo_box)
        logo_col = QVBoxLayout()
        logo_col.setSpacing(2)
        title = QLabel("RecTheWord")
        title.setObjectName("splashTitle")
        logo_col.addWidget(title)
        tagline = QLabel("实时字幕 · 智能纪要")
        tagline.setObjectName("splashTagline")
        logo_col.addWidget(tagline)
        logo_row.addLayout(logo_col)
        root.addLayout(logo_row)

        root.addSpacing(16)

        # 步骤指示器列表
        self.step_indicators: list[StepIndicator] = []
        for _, name, icon in STEPS:
            ind = StepIndicator(icon, name)
            self.step_indicators.append(ind)
            root.addWidget(ind)

        root.addSpacing(16)

        # 进度条
        self.bar = QProgressBar()
        self.bar.setRange(0, TOTAL_STEPS)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        root.addWidget(self.bar)

        # 详情文字
        self.detail_lbl = QLabel("")
        self.detail_lbl.setObjectName("splashDetail")
        self.detail_lbl.setWordWrap(True)
        root.addWidget(self.detail_lbl)

        root.addStretch(1)

        # 错误态按钮（默认隐藏）
        self.btn_row = QHBoxLayout()
        self.btn_row.setSpacing(10)
        self.retry_btn = QPushButton("重试")
        self.retry_btn.setObjectName("retryBtn")
        self.retry_btn.clicked.connect(self._on_retry)
        self.quit_btn = QPushButton("退出")
        self.quit_btn.setObjectName("quitBtn")
        self.quit_btn.clicked.connect(self.reject)
        self.btn_row.addWidget(self.retry_btn)
        self.btn_row.addWidget(self.quit_btn)
        self.btn_row_widget = QWidget()
        self.btn_row_widget.setLayout(self.btn_row)
        self.btn_row_widget.hide()
        root.addWidget(self.btn_row_widget)

        # 版本号
        ver = QLabel("v2.0 · Qwen3-ASR 0.6B · CPU")
        ver.setStyleSheet("color:#9a8f82; font-size:11px;")
        root.addWidget(ver, alignment=Qt.AlignmentFlag.AlignRight)

    def set_step(self, n: int, text: str, detail: str = "") -> None:
        """更新步骤指示器和进度条。n 是 1-based 的步骤号。"""
        self._step = n
        for i, ind in enumerate(self.step_indicators):
            step_num = i + 1
            if step_num < n:
                ind.set_state("done")
            elif step_num == n:
                # 最后一步（就绪）达成时标 done（绿勾），中间步骤标 active（蓝点）
                ind.set_state("done" if n == TOTAL_STEPS else "active")
            else:
                ind.set_state("pending")
        self.detail_lbl.setText(detail)
        self.bar.setValue(min(n, TOTAL_STEPS))
        self.step_changed.emit(text, n)

    def on_status(self, payload) -> None:
        """订阅 EventBus 'status'：把 pipeline 的状态机事件映射到 splash 进度。"""
        op, _seq, st = payload
        mapping = {
            "env_check": 1,
            "vad_load": 2,
            "asr_load": 3,
            "api_health": 4,
        }
        if op in mapping:
            step = mapping[op]
            if st.phase == Phase.WORKING:
                name = STEPS[step - 1][1]
                self.set_step(step, f"{name}…", st.message)
            elif st.phase == Phase.DONE:
                self.set_step(step + 1, "准备就绪", st.message)
            elif st.phase == Phase.ERROR:
                self.show_error_inline(STEPS[step - 1][1], st.message)
        if op == "ready":
            self.set_step(TOTAL_STEPS, "就绪", st.message or "所有组件已加载")

    def show_error(self, error_msg: str) -> None:
        """显示全屏错误态：所有步骤标红 + 错误信息 + 重试/退出按钮。"""
        self._error_shown = True
        for ind in self.step_indicators:
            ind.set_state("error")
        self.detail_lbl.setText(f"启动失败：{error_msg}")
        self.detail_lbl.setStyleSheet("color:#ff6b6b; font-size:13px;")
        self.btn_row_widget.show()
        self.bar.setValue(0)

    def show_error_inline(self, step_name: str, error_msg: str) -> None:
        """某个步骤出错：标记该步骤为 error，显示错误信息。"""
        for i, ind in enumerate(self.step_indicators):
            if STEPS[i][1] == step_name:
                ind.set_state("error")
                break
        self.detail_lbl.setText(f"{step_name}失败：{error_msg}")
        self.detail_lbl.setStyleSheet("color:#ff6b6b; font-size:13px;")

    def wait_for_user_action(self) -> None:
        """阻塞等待用户点击重试或退出（在 splash 的 event loop 中）。"""
        # 不需要特殊处理——app.exec() 会继续运行，
        # 用户点击 retry 或 quit 时会触发对应槽函数
        pass

    def _on_retry(self) -> None:
        """重试：重置所有步骤，重新触发启动序列。"""
        self._error_shown = False
        self.detail_lbl.setStyleSheet("")
        self.btn_row_widget.hide()
        for ind in self.step_indicators:
            ind.set_state("pending")
        self.bar.setValue(0)
        # 发出信号让 app.py 重新执行启动序列
        self.step_changed.emit("retry", 0)



