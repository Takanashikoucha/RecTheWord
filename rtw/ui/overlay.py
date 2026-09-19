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
        # 注意：不再加 Tool——Tool 窗口不进任务栏、不支持最小化。
        # 要去掉 Tool 才能让浮窗「最小化进任务栏」（用户明确要求）。
        # 仍是无边框 + 透明 + 置顶（悬浮字幕定位不变）。
        # 关键：显式带上 Window 基本类型 bit。否则 QWidget 顶级窗口会被 Qt 默认
        # 当作 Tool（不进任务栏、不能最小化）。带上 Window 后才能最小化进任务栏。
        self.setWindowFlags(
            Qt.WindowType.Window
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet(OVERLAY_QSS)
        self.resize(800, 280)  # 缩小默认尺寸，减少屏幕占用
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

        # 头部（拖拽区域）
        self.head_widget = QWidget()
        self.head_widget.setObjectName("ovHead")
        head = QHBoxLayout(self.head_widget)
        head.setContentsMargins(0, 0, 0, 0)
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
        # 浮窗按钮：「紧凑」+「✕ 关闭（切回主窗口）」。
        # 去掉「— 最小化」（最小化进任务栏有问题）；保留「✕」用于切回主窗口。
        for icon, tip in (("⇱", "紧凑"), ("✕", "切回主窗口")):
            b = QPushButton(icon)
            b.setObjectName("cbtn")
            b.setToolTip(tip)
            if tip == "切回主窗口":
                b.clicked.connect(self.hide_requested.emit)
            else:
                b.clicked.connect(lambda: self.setFixedWidth(600 if win.width() > 600 else 800))
            head.addWidget(b)
        lay.addWidget(self.head_widget)

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

    def recenter_bottom(self, screen_geo) -> None:
        """回到默认位置（屏幕底部居中）。再次显示浮窗时调用。"""
        self.move(screen_geo.center().x() - self.width() // 2,
                  screen_geo.bottom() - self.height() - 80)

    # ---- 拖拽 ----

    def mousePressEvent(self, e) -> None:
        """整个浮窗都可拖拽移动。

        优先用合成器原生 startSystemMove()（Wayland 下最可靠，KWin 接管拖动）；
        不可用时回退到手算 move()。子控件（按钮/滚动条）各自消费事件不冒泡。
        """
        if e.button() == Qt.MouseButton.LeftButton:
            wh = self.windowHandle()
            if wh is not None and hasattr(wh, "startSystemMove"):
                try:
                    wh.startSystemMove()
                    e.accept()
                    return
                except Exception:
                    pass
            self._drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()

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
        # 状态栏扩展：状态 + 时长 + 句数
        status = self.status_lbl.text()
        self.status_lbl.setText(f"{status} · {clock}")
