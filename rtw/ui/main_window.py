"""主窗口：顶栏 + 左面板（声道/设置）+ 右字幕舞台 + 底部 dock（mockup/index.html 实现）。

渲染进程只做展示：所有数据经 EventBus 到达（方案 §2 进程隔离）。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QProgressBar, QPushButton,
                               QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..core.status_machine import Phase, StatusState
from .theme import MAIN_QSS


class LaneCard(QFrame):
    def __init__(self, name: str, sub: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("laneCard")
        self.setObjectName(f"lane{'Mic' if name == 'mic' else 'Sys'}")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        head = QHBoxLayout()
        nm = QLabel(name.upper())
        nm.setObjectName("laneName")
        head.addWidget(nm)
        head.addStretch(1)
        self.toggle = QPushButton("●")
        self.toggle.setObjectName("iconBtn")
        self.toggle.setCheckable(True)
        self.toggle.setChecked(True)
        head.addWidget(self.toggle)
        lay.addLayout(head)

        sb = QLabel(sub)
        sb.setObjectName("laneSub")
        lay.addWidget(sb)

        dev_row = QHBoxLayout()
        lbl = QLabel("设备")
        lbl.setObjectName("fieldLbl")
        dev_row.addWidget(lbl)
        self.device = QComboBox()
        self.device.addItems(["系统默认"])
        dev_row.addWidget(self.device, 1)
        lay.addLayout(dev_row)


class MainWindow(QMainWindow):
    def __init__(self, bus, model_path: str, target_lang: str = "zh") -> None:
        super().__init__()
        self.bus = bus
        self.setWindowTitle("RecTheWord — 实时字幕与翻译")
        self.resize(1280, 760)
        self.setMinimumSize(1024, 640)
        self.setStyleSheet(MAIN_QSS)
        self._t0 = time.monotonic()
        self._sentence_count = 0
        self._line_refs: dict[str, QLabel] = {}

        central = QWidget()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_topbar())
        mid = QHBoxLayout()
        mid.setSpacing(0)
        mid.addWidget(self._build_left(), 0)
        mid.addWidget(self._build_right(), 1)
        root.addLayout(mid, 1)
        root.addWidget(self._build_dock())

        # 状态 toast（等待态机制的 UI 呈现）
        self.toast = QFrame()
        self.toast.setObjectName("toast")
        self.toast.hide()
        tl = QHBoxLayout(self.toast)
        self.toast_msg = QLabel("")
        self.toast_msg.setObjectName("toastMsg")
        self.toast_eta = QLabel("")
        self.toast_eta.setObjectName("toastEta")
        tl.addWidget(self.toast_msg)
        tl.addWidget(self.toast_eta)
        central.layout().addWidget(self.toast)
        self.toast.move(20, 60)

        # 时钟
        clk = QTimer(self)
        clk.timeout.connect(self._tick)
        clk.start(1000)

        # 订阅事件
        bus.subscribe("status", self.on_status)
        bus.subscribe("asr", self.on_asr)
        bus.subscribe("trans", self.on_trans)
        bus.subscribe("trans_err", self.on_trans_err)
        bus.subscribe("seg", self.on_seg)

    # ---------- 构建 ----------

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setFixedHeight(52)
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(18, 0, 18, 0)
        lay.setSpacing(16)

        logo = QLabel("R")
        logo.setObjectName("logoBox")
        logo.setFixedSize(26, 26)
        logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lt = QLabel("R")
        lt.setObjectName("logoText")
        logo.setLayout(None)
        lay.addWidget(logo)
        brand = QLabel("RecTheWord")
        brand.setObjectName("brandLabel")
        lay.addWidget(brand)

        pill = QLabel("● 录音中")
        pill.setObjectName("sessionPill")
        lay.addWidget(pill)
        self.timer_lbl = QLabel("00:00:00")
        self.timer_lbl.setObjectName("timerLabel")
        lay.addWidget(self.timer_lbl)
        lay.addStretch(1)

        for text, obj in (("导出记录", "exportBtn"), ("生成会议纪要", "minutesBtn")):
            b = QPushButton(text)
            b.setObjectName(obj)
            if obj == "minutesBtn":
                b.setObjectName("primaryBtn")
            lay.addWidget(b)
        return bar

    def _build_left(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("leftPanel")
        panel.setFixedWidth(330)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 16)
        lay.setSpacing(14)

        t = QLabel("声道")
        t.setObjectName("laneTitle")
        lay.addWidget(t)
        self.lane_mic = LaneCard("麦克风", "WASAPI 输入 · 实时")
        self.lane_sys = LaneCard("扬声器", "WASAPI 环回 · 对方声音")
        lay.addWidget(self.lane_mic)
        lay.addWidget(self.lane_sys)

        lay.addSpacing(8)
        for title in ("识别引擎", "说话人分离", "字幕外观"):
            h = QLabel(title)
            h.setObjectName("sectionHead")
            lay.addWidget(h)

        eng = QLabel("Qwen3-ASR 0.6B · CPU · int8")
        eng.setObjectName("laneSub")
        lay.addWidget(eng)

        sep_row = QHBoxLayout()
        sep_row.addWidget(QLabel("会后聚类标注"))
        sep_row.addStretch(1)
        sep_toggle = QPushButton("●")
        sep_toggle.setObjectName("iconBtn")
        sep_toggle.setCheckable(True)
        sep_toggle.setChecked(True)
        sep_row.addWidget(sep_toggle)
        lay.addLayout(sep_row)

        note = QLabel("实时阶段按能量粗分，会议结束后离线精修")
        note.setObjectName("laneSub")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.font_spin = QComboBox()
        self.font_spin.addItems(["24 px", "28 px", "32 px"])
        self.font_spin.setCurrentIndex(1)
        lay.addWidget(self.font_spin)

        lay.addStretch(1)
        return panel

    def _build_right(self) -> QWidget:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)

        header = QWidget()
        header.setObjectName("subHeader")
        header.setFixedHeight(46)
        hl = QHBoxLayout(header)
        hl.setContentsMargins(20, 0, 20, 0)
        tabs = QWidget()
        tabs.setObjectName("tabBase")
        tb = QHBoxLayout(tabs)
        tb.setContentsMargins(0, 0, 0, 0)
        tb.setSpacing(4)
        for i, name in enumerate(("实时字幕", "完整记录", "说话人")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(i == 0)
            tb.addWidget(b)
        hl.addWidget(tabs)
        hl.addStretch(1)
        lat = QLabel("端到端延迟 ")
        lat.setObjectName("latencyLbl")
        hl.addWidget(lat)
        self.lat_val = QLabel("-")
        self.lat_val.setObjectName("latVal")
        hl.addWidget(self.lat_val)
        lay.addWidget(header)

        stage = QScrollArea()
        stage.setObjectName("stage")
        stage.setWidgetResizable(True)
        stage.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        self.sub_layout = QVBoxLayout(container)
        self.sub_layout.setContentsMargins(60, 24, 60, 96)
        self.sub_layout.setSpacing(14)
        div = QLabel("· 会议开始 ·")
        div.setAlignment(Qt.AlignmentFlag.AlignCenter)
        div.setStyleSheet("color:#48546a; font-size:11px; font-family:Consolas,monospace;")
        self.sub_layout.addWidget(div)
        self.sub_layout.addStretch(1)
        stage.setWidget(container)
        lay.addWidget(stage, 1)
        self.stage_scroll = stage
        return w

    def _build_dock(self) -> QWidget:
        dock = QWidget()
        dock.setObjectName("dock")
        dock.setFixedHeight(64)
        lay = QHBoxLayout(dock)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(14)

        play = QPushButton("❚❚")
        play.setObjectName("playBtn")
        play.setCheckable(True)
        lay.addWidget(play)

        for k, v in (("时长", "00:00"), ("句子数", "0"), ("说话人", "-"), ("磁盘", "0 MB")):
            stat = QVBoxLayout()
            kl = QLabel(k)
            kl.setObjectName("statK")
            vl = QLabel(v)
            vl.setObjectName("statV")
            vl.setProperty("statKey", k)
            stat.addWidget(kl)
            stat.addWidget(vl)
            lay.addLayout(stat)
            if k == "句子数":
                self.sent_cnt = vl
            if k == "时长":
                self.dock_timer = vl
        lay.addStretch(1)
        exp = QPushButton("导出记录")
        lay.addWidget(exp)
        mins = QPushButton("生成会议纪要")
        mins.setObjectName("primaryBtn")
        lay.addWidget(mins)
        return dock

    # ---------- 事件处理 ----------

    def on_status(self, payload) -> None:
        op, _seq, st = payload
        if st.phase == Phase.WORKING:
            self.toast.show()
            self.toast_msg.setText(st.message or op)
            eta = f"~{int(st.eta_s)}s" if st.eta_s else ""
            self.toast_eta.setText(eta)
            self.toast.adjustSize()
            self.toast.move(20, 60)
        elif st.phase in (Phase.DONE, Phase.ERROR):
            self.toast_msg.setText(f"{'✓ ' if st.phase == Phase.DONE else '✗ '}{st.message}")
            self.toast_eta.setText("")
            # 3 秒后收起
            QTimer.singleShot(3000, self.toast.hide)

    def on_asr(self, p: dict) -> None:
        self._sentence_count += 1
        self.sent_cnt.setText(str(self._sentence_count))
        lane_tag = "麦" if p["lane"] == "mic" else "扬"
        tag = QLabel(lane_tag)
        tag.setObjectName("tagMic" if p["lane"] == "mic" else "tagSys")
        tag.setFixedWidth(52)
        tag.setAlignment(Qt.AlignmentFlag.AlignCenter)

        col = QVBoxLayout()
        col.setSpacing(3)
        src = QLabel(p["text"])
        src.setObjectName("srcText")
        src.setWordWrap(True)
        tr = QLabel("")
        tr.setObjectName("trText")
        tr.setWordWrap(True)
        ts = QLabel(f"{p.get('t_first_ms', 0)} ms")
        ts.setObjectName("tsText")
        col.addWidget(src)
        col.addWidget(tr)
        col.addWidget(ts)

        line = QFrame()
        line.setObjectName("subLine")
        row = QHBoxLayout(line)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(14)
        row.addWidget(tag)
        row.addLayout(col)

        self.sub_layout.insertWidget(self.sub_layout.count() - 1, line)
        self._trim_lines()
        self.stage_scroll.verticalScrollBar().setValue(
            self.stage_scroll.verticalScrollBar().maximum())
        # 存引用供译文回填
        self._line_refs[p["seg_id"]] = tr
        # 延迟显示
        self.lat_val.setText(f"{p.get('t_first_ms', 0)} ms")

    def on_trans(self, p: dict) -> None:
        tr = self._line_refs.get(p["seg_id"])
        if tr:
            tr.setText(tr.text() + p["delta"])

    def on_trans_err(self, p: dict) -> None:
        tr = self._line_refs.get(p["seg_id"])
        if tr:
            tr.setText(f"⚠ {p['error']}")

    def on_seg(self, p: dict) -> None:
        pass  # 预留：波形/能量显示

    def _trim_lines(self) -> None:
        while self.sub_layout.count() > 15:
            item = self.sub_layout.itemAt(1)
            if item and item.widget():
                item.widget().deleteLater()
                self.sub_layout.removeItemAt(1)

    def _tick(self) -> None:
        s = int(time.monotonic() - self._t0)
        self.timer_lbl.setText(f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
        self.dock_timer.setText(f"{s // 60:02d}:{s % 60:02d}")
