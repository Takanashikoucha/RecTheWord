"""透明字幕浮窗：无边框 + 半透明 + 可拖拽（mockup/overlay.html 的 PySide6 实现）。

会议中只有这个窗口显示（用户决策）；主窗口可最小化。
主题：glass（默认）/ solid / outline / light。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QPoint, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QPushButton,
                               QScrollArea, QVBoxLayout, QWidget)

from .theme import LIGHT_OVERRIDE, OVERLAY_QSS

THEMES = ("glass", "solid", "outline", "light")


class SubLineWidget(QFrame):
    """一条字幕：徽标（麦/扬）+ 原文 + 译文 + 时间戳。interim/final 两态。"""

    def __init__(self, lane: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ovLine")
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(12)

        badge = QLabel("麦" if lane == "mic" else "扬")
        badge.setObjectName("ovTagMic" if lane == "mic" else "ovTagSys")
        badge.setFixedWidth(34)
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(badge)

        col = QVBoxLayout()
        col.setSpacing(2)
        self.src = QLabel("")
        self.src.setObjectName("ovSrc")
        self.src.setWordWrap(True)
        self.tr = QLabel("")
        self.tr.setObjectName("ovTr")
        self.tr.setWordWrap(True)
        self.ts = QLabel("")
        self.ts.setObjectName("ovTs")
        col.addWidget(self.src)
        col.addWidget(self.tr)
        col.addWidget(self.ts)
        row.addLayout(col)

    def set_interim(self, text: str) -> None:
        self.src.setText(text)
        self.src.setObjectName("ovSrcInterim")
        self.tr.setText("")

    def set_final(self, text: str, ts: str = "") -> None:
        self.src.setText(text)
        self.src.setObjectName("ovSrc")
        self.ts.setText(ts)

    def append_translation(self, delta: str) -> None:
        self.tr.setText(self.tr.text() + delta)


class OverlayWindow(QWidget):
    """顶层透明浮窗。"""

    hide_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(OVERLAY_QSS)
        self.resize(980, 220)
        self._drag_pos: QPoint | None = None
        self._theme = "glass"
        self._clock_start = time.monotonic()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        win = QFrame()
        win.setObjectName("ovWin")
        outer.addWidget(win)
        lay = QVBoxLayout(win)
        lay.setContentsMargins(22, 16, 22, 18)
        lay.setSpacing(12)

        # 头部
        head = QHBoxLayout()
        dot = QLabel()
        dot.setObjectName("ovDot")
        dot.setFixedSize(8, 8)
        head.addWidget(dot)
        title = QLabel("REC THE WORD · 实时字幕")
        title.setObjectName("ovTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.meta = QLabel("延迟 <b>-</b>")
        self.meta.setObjectName("ovMeta")
        self.meta.setTextFormat(Qt.TextFormat.RichText)
        head.addWidget(self.meta)
        for icon, tip in (("⇱", "紧凑"), ("—", "隐藏"), ("✕", "关闭")):
            b = QPushButton(icon)
            b.setObjectName("cbtn")
            b.setToolTip(tip)
            if tip == "关闭":
                b.clicked.connect(self.hide_requested.emit)
            elif tip == "隐藏":
                b.clicked.connect(self.hide)
            else:
                b.clicked.connect(lambda: self.setFixedWidth(720 if win.width() > 720 else 980))
            head.addWidget(b)
        lay.addLayout(head)

        # 字幕区（垂直滚动，最新在下）
        # 关键：scroll 自身、viewport、container 三层都要关掉自动填充背景，
        # 否则会盖住浮窗的半透明底（白色块 bug）
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        scroll.setAutoFillBackground(False)
        scroll.viewport().setAutoFillBackground(False)
        container = QWidget()
        container.setAutoFillBackground(False)
        self.lines_layout = QVBoxLayout(container)
        self.lines_layout.setSpacing(8)
        self.lines_layout.addStretch(1)
        scroll.setWidget(container)
        lay.addWidget(scroll, 1)
        self.scroll = scroll

        # 底部状态
        self.status_lbl = QLabel("等待语音…")
        self.status_lbl.setObjectName("ovMeta")
        lay.addWidget(self.status_lbl)

    # ---- 对外接口（EventBus 驱动）----

    def add_line(self, lane: str) -> SubLineWidget:
        w = SubLineWidget(lane)
        self.lines_layout.insertWidget(self.lines_layout.count() - 1, w)
        # 限制条数
        while self.lines_layout.count() > 9:
            old = self.lines_layout.itemAt(1).widget()
            if old:
                old.deleteLater()
                self.lines_layout.removeItemAt(1)
        self.scroll.verticalScrollBar().setValue(
            self.scroll.verticalScrollBar().maximum())
        return w

    def set_status(self, text: str) -> None:
        self.status_lbl.setText(text)

    def set_theme(self, theme: str) -> None:
        if theme not in THEMES:
            return
        self._theme = theme
        self.setStyleSheet(OVERLAY_QSS + ("\n" + LIGHT_OVERRIDE if theme == "light" else ""))

    # ---- 拖拽 ----

    def mousePressEvent(self, e) -> None:
        if e.button() == Qt.MouseButton.LeftButton:
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e) -> None:
        if self._drag_pos and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)

    def mouseReleaseEvent(self, e) -> None:
        self._drag_pos = None

    def tick_clock(self) -> None:
        s = int(time.monotonic() - self._clock_start)
        clock = f"{s // 60:02d}:{s % 60:02d}"
        self.meta.setText(f"延迟 <b>-</b> · {clock}")
