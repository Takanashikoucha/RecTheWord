"""主窗口：顶栏 + 左面板（声道/设置）+ 右字幕舞台 + 底部 dock（mockup/index.html 实现）。

渲染进程只做展示：所有数据经 EventBus 到达（方案 §2 进程隔离）。
"""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QLabel,
                               QMainWindow, QProgressBar, QPushButton,
                               QScrollArea, QStackedWidget, QVBoxLayout,
                               QWidget)

from ..core.status_machine import Phase, StatusState
from .theme import MAIN_QSS


class LaneCard(QFrame):
    """声道卡片：名称 + 开关 + 设备下拉（可刷新 / 运行时切换）。"""

    refresh_requested = Signal(str)      # 参数：lane 名
    device_switched = Signal(str, object)  # 参数：lane 名, device_index(None=默认)

    def __init__(self, name: str, sub: str, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("laneCard")
        self.setObjectName(f"lane{'Mic' if name == 'mic' else 'Sys'}")
        self._lane_name = name
        self._devices: list[dict] = []
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
        self.device.addItem("系统默认")
        self.device.currentIndexChanged.connect(self._on_device_changed)
        dev_row.addWidget(self.device, 1)
        self.refresh_btn = QPushButton("⟳")
        self.refresh_btn.setObjectName("iconBtn")
        self.refresh_btn.setToolTip("刷新设备列表")
        self.refresh_btn.clicked.connect(self._on_refresh_clicked)
        dev_row.addWidget(self.refresh_btn)
        lay.addLayout(dev_row)

    # ---- 设备列表 / 刷新 / 切换 ----

    def set_devices(self, devices: list[dict]) -> None:
        """填充设备下拉（第一项恒为「系统默认」= index None）。"""
        self._devices = devices
        self.device.blockSignals(True)
        self.device.clear()
        self.device.addItem("系统默认")
        for d in devices:
            self.device.addItem(d["name"], d["index"])
        self.device.blockSignals(False)

    def _on_refresh_clicked(self) -> None:
        self.refresh_requested.emit(self._lane_name)

    def _on_device_changed(self, idx: int) -> None:
        dev_idx = None if idx <= 0 else self.device.itemData(idx)
        self.device_switched.emit(self._lane_name, dev_idx)


class MainWindow(QMainWindow):
    def __init__(self, bus, model_path: str, target_lang: str = "zh",
                 pipeline=None) -> None:
        super().__init__()
        self.bus = bus
        self.pipeline = pipeline  # 用于设备刷新 / 热切换（可选，测试可不传）
        self.setWindowTitle("RecTheWord — 实时字幕与翻译")
        self.resize(1280, 760)
        self.setMinimumSize(1024, 640)
        self.setStyleSheet(MAIN_QSS)
        self._t0 = time.monotonic()
        self._sentence_count = 0
        self._line_refs: dict[str, QLabel] = {}
        self._paused = False
        self._stopped = False

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

        self.session_pill = QLabel("● 录音中")
        self.session_pill.setObjectName("sessionPillLive")
        lay.addWidget(self.session_pill)
        self.timer_lbl = QLabel("00:00:00")
        self.timer_lbl.setObjectName("timerLabel")
        lay.addWidget(self.timer_lbl)
        lay.addStretch(1)

        tb_exp = QPushButton("导出记录")
        tb_exp.setObjectName("exportBtn")
        tb_exp.clicked.connect(self._on_export)
        lay.addWidget(tb_exp)
        tb_min = QPushButton("生成会议纪要")
        tb_min.setObjectName("primaryBtn")
        tb_min.clicked.connect(self._on_generate_minutes)
        lay.addWidget(tb_min)
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
        # 设备刷新 / 运行时热切换
        for card in (self.lane_mic, self.lane_sys):
            card.refresh_requested.connect(self._on_refresh_devices)
            card.device_switched.connect(self._on_device_switched)
        self._load_devices()

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
        self.sep_toggle = QPushButton("●")
        self.sep_toggle.setObjectName("iconBtn")
        self.sep_toggle.setCheckable(True)
        self.sep_toggle.setChecked(True)
        self.sep_toggle.setToolTip("开启/关闭会后离线精修说话人聚类")
        self.sep_toggle.toggled.connect(self._on_sep_toggled)
        sep_row.addWidget(self.sep_toggle)
        lay.addLayout(sep_row)

        note = QLabel("实时阶段按能量粗分，会议结束后离线精修")
        note.setObjectName("laneSub")
        note.setWordWrap(True)
        lay.addWidget(note)

        self.font_spin = QComboBox()
        self.font_spin.addItems(["24 px", "28 px", "32 px"])
        self.font_spin.setCurrentIndex(1)
        self.font_spin.currentIndexChanged.connect(self._on_font_changed)
        lay.addWidget(self.font_spin)

        lay.addStretch(1)
        return panel

    # ---- 设备管理（刷新 / 运行时热切换）----

    def _load_devices(self) -> None:
        """初始加载设备列表（非 Windows / 无 pipeline 时保持「系统默认」）。"""
        if self.pipeline is None:
            return
        try:
            devs = self.pipeline.list_devices()
        except Exception:
            return
        self.lane_mic.set_devices(devs.get("mic", []))
        self.lane_sys.set_devices(devs.get("sys", []))

    def _on_refresh_devices(self, _lane: str) -> None:
        """⟳ 按钮：重新枚举设备（拔插耳机 / 切换输出后）。"""
        if self.pipeline is None:
            return
        try:
            devs = self.pipeline.refresh_devices()
        except Exception as e:
            self._toast(f"设备刷新失败：{e}")
            return
        self.lane_mic.set_devices(devs.get("mic", []))
        self.lane_sys.set_devices(devs.get("sys", []))
        self._toast("设备列表已刷新")

    def _on_device_switched(self, lane: str, device_index) -> None:
        """下拉切换：运行时热切换（自动重连，不打断下游 VAD）。"""
        if self.pipeline is None:
            return
        try:
            self.pipeline.switch_device(lane, device_index)
            name = "系统默认" if device_index is None else f"设备 #{device_index}"
            self._toast(f"{lane} 已切换到 {name}")
        except Exception as e:
            self._toast(f"切换失败：{e}")

    # ---- 导出 / 纪要 / 聚类 / 字号 ----

    def _on_export(self) -> None:
        """导出记录：markdown 落盘 + toast 提示路径。"""
        if self.pipeline is None:
            self._toast("导出失败：管线未连接")
            return
        try:
            path = self.pipeline.store.export_markdown()
            self._toast(f"已导出：{path}")
        except Exception as e:
            self._toast(f"导出失败：{e}")

    def _on_generate_minutes(self) -> None:
        """生成会议纪要：流式进度（等待态显式告知），完成后落盘。"""
        if self.pipeline is None:
            self._toast("纪要失败：管线未连接")
            return
        self._toast("正在生成会议纪要…")

        def _delta(d: str) -> None:
            pass  # 增量累积在 generator 内部

        def _done() -> None:
            self._toast("✓ 会议纪要已生成并保存")

        def _err(e) -> None:
            self._toast(f"纪要失败：{e}")

        try:
            self.pipeline.generate_minutes(_delta, _done, _err)
        except Exception as e:
            self._toast(f"纪要失败：{e}")

    def _on_sep_toggled(self, checked: bool) -> None:
        """会后聚类标注开关。"""
        if self.pipeline is not None:
            self.pipeline.diarizer.enabled = checked
        self._toast("会后聚类已开启" if checked else "会后聚类已关闭")

    def _on_font_changed(self, idx: int) -> None:
        """字幕字号（透传给浮窗）。"""
        sizes = (24, 28, 32)
        if hasattr(self, "_overlay_ref"):
            self._overlay_ref.set_font_size(sizes[idx])
        self._toast(f"字幕字号 {sizes[idx]} px")

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

        self.play_btn = QPushButton("❚❚")
        self.play_btn.setObjectName("playBtn")
        self.play_btn.setCheckable(True)
        self.play_btn.setToolTip("暂停 / 继续")
        self.play_btn.toggled.connect(self._on_pause_toggled)
        lay.addWidget(self.play_btn)

        self.stop_btn = QPushButton("■")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setToolTip("停止（结束会话并归档）")
        self.stop_btn.clicked.connect(self._on_stop_clicked)
        lay.addWidget(self.stop_btn)

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
            if k == "说话人":
                self.spk_cnt = vl
            if k == "磁盘":
                self.disk_lbl = vl
        lay.addStretch(1)
        exp = QPushButton("导出记录")
        exp.clicked.connect(self._on_export)
        lay.addWidget(exp)
        mins = QPushButton("生成会议纪要")
        mins.setObjectName("primaryBtn")
        mins.clicked.connect(self._on_generate_minutes)
        lay.addWidget(mins)
        return dock

    # ---------- 事件处理 ----------

    # ---- 录音控制（暂停 / 停止）----

    def _set_pill(self, text: str, obj: str) -> None:
        self.session_pill.setText(text)
        self.session_pill.setObjectName(obj)
        self.session_pill.style().unpolish(self.session_pill)
        self.session_pill.style().polish(self.session_pill)

    def _on_pause_toggled(self, checked: bool) -> None:
        """暂停/继续：冻结音频采集（VAD 不切句），UI 状态同步。"""
        self._paused = checked
        if self.pipeline is not None:
            try:
                if checked:
                    self.pipeline.pause()
                else:
                    self.pipeline.resume()
            except Exception as e:
                self._toast(f"{'暂停' if checked else '继续'}失败：{e}")
        self._set_pill("❚❚ 已暂停", "sessionPillPaused")
        self._toast("已暂停" if checked else "已继续")
        if not checked:
            self._set_pill("● 录音中", "sessionPillLive")

    def _on_stop_clicked(self) -> None:
        """停止：结束会话 + 归档（此后零后台计算）。"""
        self._stopped = True
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self._set_pill("■ 已停止", "sessionPillStopped")
        if self.pipeline is not None:
            try:
                self.pipeline.stop()
            except Exception as e:
                self._toast(f"停止失败：{e}")
        self._toast("已停止 · 会话已归档")

    def _toast(self, text: str) -> None:
        """瞬时提示（设备刷新 / 切换结果），3 秒后收起。"""
        self.toast.show()
        self.toast_msg.setText(text)
        self.toast_eta.setText("")
        self.toast.adjustSize()
        self.toast.move(20, 60)
        QTimer.singleShot(3000, self.toast.hide)

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
            item = self.sub_layout.takeAt(1)
            if item and item.widget():
                item.widget().deleteLater()

    def _tick(self) -> None:
        s = int(time.monotonic() - self._t0)
        self.timer_lbl.setText(f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}")
        self.dock_timer.setText(f"{s // 60:02d}:{s % 60:02d}")
        # 说话人数 / 磁盘占用（真实值）
        if self.pipeline is not None:
            try:
                n_spk = len(set(
                    d.get("speaker") for d in self.pipeline.store.iter_transcript()
                    if d.get("speaker")))
                if n_spk:
                    self.spk_cnt.setText(str(n_spk))
                sess = self.pipeline.store.current
                if sess and sess.exists():
                    size = sum(f.stat().st_size for f in sess.rglob("*") if f.is_file())
                    self.disk_lbl.setText(f"{size / 1024 / 1024:.1f} MB")
            except Exception:
                pass
