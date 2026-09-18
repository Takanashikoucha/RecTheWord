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

    def set_font_size(self, px: int) -> None:
        from PySide6.QtGui import QFont
        f = QFont()
        f.setPixelSize(px)
        self.src.setFont(f)
        self.tr.setFont(f)


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
        self.resize(980, 340)  # 上下两分区需要更高
        self._drag_pos: QPoint | None = None
        self._theme = "glass"
        self._clock_start = time.monotonic()
        self._last_latency: int | None = None

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

        # 字幕区：上下两分区（🎤 麦 上 / 🔊 扬 下），各自独立滚动
        # 关键：scroll 自身、viewport、container 三层都要关掉自动填充背景，
        # 否则会盖住浮窗的半透明底（白色块 bug）
        self.sections: dict[str, dict] = {}
        for sec_name, sec_title, sec_obj in (
            ("mic", "🎤 麦克风", "secMic"),
            ("sys", "🔊 扬声器", "secSys"),
        ):
            sec_head = QLabel(sec_title)
            sec_head.setObjectName(sec_obj)
            lay.addWidget(sec_head)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            scroll.setAutoFillBackground(False)
            scroll.viewport().setAutoFillBackground(False)
            container = QWidget()
            container.setAutoFillBackground(False)
            lines_layout = QVBoxLayout(container)
            lines_layout.setContentsMargins(0, 0, 0, 0)
            lines_layout.setSpacing(6)
            lines_layout.addStretch(1)
            scroll.setWidget(container)
            lay.addWidget(scroll, 1)
            self.sections[sec_name] = {"layout": lines_layout, "scroll": scroll}
        # 向后兼容：lines_layout 指向 mic 区
        self.lines_layout = self.sections["mic"]["layout"]
        self.scroll = self.sections["mic"]["scroll"]

        # 底部状态
        self.status_lbl = QLabel("待机")
        self.status_lbl.setObjectName("ovMeta")
        lay.addWidget(self.status_lbl)

    # ---- 对外接口（EventBus 驱动）----

    def add_line(self, lane: str) -> SubLineWidget:
        sec = self.sections.get(lane, self.sections["mic"])
        layout = sec["layout"]
        w = SubLineWidget(lane)
        layout.insertWidget(layout.count() - 1, w)
        # 每区限 5 条
        while layout.count() > 6:
            item = layout.takeAt(1)
            old = item.widget() if item else None
            if old:
                old.deleteLater()
        sec["scroll"].verticalScrollBar().setValue(
            sec["scroll"].verticalScrollBar().maximum())
        return w

    def set_status(self, text: str) -> None:
        self.status_lbl.setText(text)

    def set_latency(self, ms: int | None) -> None:
        """更新延迟显示（最近一段的端到端延迟）。"""
        self._last_latency = ms

    def set_font_size(self, px: int) -> None:
        """字幕字号（主窗口字号选择器透传）。"""
        self._font_px = px
        for sec in self.sections.values():
            for i in range(sec["layout"].count()):
                w = sec["layout"].itemAt(i).widget()
                if isinstance(w, SubLineWidget):
                    w.set_font_size(px)

    def set_running(self, running: bool, paused: bool = False) -> None:
        """录音状态 → 状态栏文案（纯状态词，不与「等待语音」矛盾）。"""
        if not running:
            self.set_status("已停止")
        elif paused:
            self.set_status("已暂停")
        else:
            self.set_status("● 录音中")

    # 四态便捷方法（待机 / 录音 / 暂停 / 停止）
    def set_idle(self) -> None:
        self.set_status("待机")

    def set_live(self) -> None:
        self.set_status("● 录音中")

    def set_paused(self) -> None:
        self.set_status("已暂停")

    def set_stopped(self) -> None:
        self.set_status("已停止")

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
        lat = f"<b>{self._last_latency}</b>" if self._last_latency is not None else "<b>-</b>"
        self.meta.setText(f"延迟 {lat} ms · {clock}")
