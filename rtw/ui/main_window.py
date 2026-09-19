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
    toggle_overlay_requested = Signal()
    minimize_requested = Signal()

    def __init__(self, bus, model_path: str, target_lang: str = "zh",
                 pipeline=None) -> None:
        super().__init__()
        self.bus = bus
        self.pipeline = pipeline  # 用于设备刷新 / 热切换（可选，测试可不传）
        self.overlay = None       # 由 app.py 装配后 attach
        self.setWindowTitle("RecTheWord — 实时字幕与翻译")
        # 去原生边框：自定义标题栏（自绘 —/✕）。
        # 注意：只加 FramelessWindowHint，绝不再加 WindowStaysOnTopHint——
        # 后者在 Wayland/KDE 下会把窗口提升到置顶层，导致不进任务栏、最小化失效。
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.resize(1280, 760)
        self.setMinimumSize(1024, 640)
        self.setStyleSheet(MAIN_QSS)
        self._drag_pos = None
        self._title_bar = None
        self._t0 = None            # 本次录制开始的 monotonic 时刻（None = 尚未开始）
        self._accum = 0.0          # 已累计的录制秒数（跨多次 开始/暂停/停止）
        self._pause_at = None      # 本次暂停起点（None = 未在暂停中）
        self._sentence_count = 0
        self._line_refs: dict[str, QLabel] = {}
        self._full_line_refs: dict[str, QLabel] = {}
        self._paused = False
        self._stopped = False
        self._started = False

        central = QWidget()
        central.setObjectName("centralRoot")
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
        bus.subscribe("notice", self.on_notice)

    # ---------- 构建 ----------

    def _build_topbar(self) -> QWidget:
        bar = QWidget()
        bar.setObjectName("topbar")
        bar.setFixedHeight(52)
        self._title_bar = bar  # 拖动 / 双击最大化的命中区域
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

        self.session_pill = QLabel("待机")
        self.session_pill.setObjectName("sessionPillIdle")
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
        self.ov_toggle = QPushButton("🪟 浮窗")
        self.ov_toggle.setObjectName("ghostBtn")
        self.ov_toggle.setToolTip("切换字幕浮窗显隐")
        self.ov_toggle.clicked.connect(self.toggle_overlay_requested.emit)
        lay.addWidget(self.ov_toggle)
        # 自定义窗口控件：— 切到悬浮界面 / ✕ 退出进程
        self.win_min = QPushButton("—")
        self.win_min.setObjectName("ghostBtn")
        self.win_min.setToolTip("最小化（切换到悬浮窗界面）")
        self.win_min.clicked.connect(self.minimize_requested.emit)
        lay.addWidget(self.win_min)
        self.win_close = QPushButton("✕")
        self.win_close.setObjectName("ghostBtn")
        self.win_close.setToolTip("关闭（退出整个程序）")
        # 关键：连 app.quit() 而非 self.close()——self.close() 只关主窗，
        # 浮窗还开着时 QApplication 不退出，进程就退不出。
        self.win_close.clicked.connect(self._quit_app)
        lay.addWidget(self.win_close)
        return bar

    def _quit_app(self) -> None:
        """退出整个程序（触发 aboutToQuit → pipe.stop 收尾）。"""
        from PySide6.QtWidgets import QApplication
        inst = QApplication.instance()
        if inst is not None:
            inst.quit()
        else:
            self.close()

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
        import platform
        if platform.system() == "Windows":
            mic_sub, sys_sub = "WASAPI 输入 · 实时", "WASAPI 环回 · 对方声音"
        else:
            mic_sub, sys_sub = "ALSA 输入 · 实时", "扬声器环回 · 对方声音"
        self.lane_mic = LaneCard("麦克风", mic_sub)
        self.lane_sys = LaneCard("扬声器", sys_sub)
        lay.addWidget(self.lane_mic)
        lay.addWidget(self.lane_sys)
        # 设备刷新 / 运行时热切换
        for card in (self.lane_mic, self.lane_sys):
            card.refresh_requested.connect(self._on_refresh_devices)
            card.device_switched.connect(self._on_device_switched)
        self._load_devices()

        lay.addSpacing(8)

        # 识别引擎（标题紧跟其内容）
        h1 = QLabel("识别引擎")
        h1.setObjectName("sectionHead")
        lay.addWidget(h1)
        eng_text = "Qwen3-ASR 0.6B · CPU · int8"
        if self.pipeline is not None:
            acfg = self.pipeline.cfg.asr
            eng_text = f"{acfg.model} · {acfg.device} · {acfg.compute_type}"
        eng = QLabel(eng_text)
        eng.setObjectName("laneSub")
        lay.addWidget(eng)

        # 说话人分离（标题紧跟其内容）
        h2 = QLabel("说话人分离")
        h2.setObjectName("sectionHead")
        lay.addWidget(h2)
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
        note = QLabel("按音量变化自动猜测说话人（仅供参考，会后精修）")
        note.setObjectName("laneSub")
        note.setWordWrap(True)
        lay.addWidget(note)

        # 字幕外观（标题紧跟其内容）
        h3 = QLabel("字幕外观")
        h3.setObjectName("sectionHead")
        lay.addWidget(h3)
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
        if self.pipeline.store.current is None:
            self._toast("暂无记录可导出（请先开始会议）")
            return
        try:
            path = self.pipeline.store.export_markdown()
            if not path:
                self._toast("暂无记录可导出")
            else:
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
        self.tab_buttons: list[QPushButton] = []
        for i, name in enumerate(("实时字幕", "完整记录")):
            b = QPushButton(name)
            b.setCheckable(True)
            b.setChecked(i == 0)
            b.clicked.connect(lambda _checked, idx=i: self._on_tab_clicked(idx))
            tb.addWidget(b)
            self.tab_buttons.append(b)
        hl.addWidget(tabs)
        hl.addStretch(1)
        lat = QLabel("端到端延迟 ")
        lat.setObjectName("latencyLbl")
        hl.addWidget(lat)
        self.lat_val = QLabel("-")
        self.lat_val.setObjectName("latVal")
        hl.addWidget(self.lat_val)
        lay.addWidget(header)

        # QStackedWidget：实时字幕（trim 15 条）+ 完整记录（不 trim）
        self.stack = QStackedWidget()

        # Page 0：实时字幕
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
        self.stack.addWidget(stage)
        self.stage_scroll = stage

        # Page 1：完整记录（不 trim，显示所有历史句子）
        full_stage = QScrollArea()
        full_stage.setObjectName("stage")
        full_stage.setWidgetResizable(True)
        full_stage.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        full_container = QWidget()
        self.full_layout = QVBoxLayout(full_container)
        self.full_layout.setContentsMargins(60, 24, 60, 96)
        self.full_layout.setSpacing(14)
        full_div = QLabel("· 完整记录 ·")
        full_div.setAlignment(Qt.AlignmentFlag.AlignCenter)
        full_div.setStyleSheet("color:#48546a; font-size:11px; font-family:Consolas,monospace;")
        self.full_layout.addWidget(full_div)
        self.full_layout.addStretch(1)
        full_stage.setWidget(full_container)
        self.stack.addWidget(full_stage)
        self.full_stage_scroll = full_stage

        lay.addWidget(self.stack, 1)
        return w

    def _on_tab_clicked(self, idx: int) -> None:
        """切换 tab：实时字幕 ↔ 完整记录。"""
        for i, b in enumerate(self.tab_buttons):
            b.setChecked(i == idx)
        self.stack.setCurrentIndex(idx)

    def _build_dock(self) -> QWidget:
        dock = QWidget()
        dock.setObjectName("dock")
        dock.setFixedHeight(64)
        lay = QHBoxLayout(dock)
        lay.setContentsMargins(20, 0, 20, 0)
        lay.setSpacing(14)

        self.start_btn = QPushButton("▶ 开始会议")
        self.start_btn.setObjectName("primaryBtn")
        self.start_btn.setToolTip("开始会议：建立会话并开始双通道采集")
        self.start_btn.clicked.connect(self._on_start_clicked)
        lay.addWidget(self.start_btn)

        self.play_btn = QPushButton("❚❚")
        self.play_btn.setObjectName("playBtn")
        self.play_btn.setCheckable(True)
        self.play_btn.setToolTip("暂停 / 继续")
        self.play_btn.setEnabled(False)
        self.play_btn.toggled.connect(self._on_pause_toggled)
        lay.addWidget(self.play_btn)

        self.stop_btn = QPushButton("■")
        self.stop_btn.setObjectName("stopBtn")
        self.stop_btn.setToolTip("停止（结束会话并归档）")
        self.stop_btn.setEnabled(False)
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
        self.refine_btn = QPushButton("精修说话人")
        self.refine_btn.setToolTip("会后离线精修说话人（手动）")
        self.refine_btn.setEnabled(False)
        self.refine_btn.clicked.connect(self._on_refine_speakers)
        lay.addWidget(self.refine_btn)
        self.mins_btn = QPushButton("生成会议纪要")
        self.mins_btn.setObjectName("primaryBtn")
        self.mins_btn.setToolTip("会后生成 AI 纪要（手动，停止后可用）")
        self.mins_btn.setEnabled(False)
        self.mins_btn.clicked.connect(self._on_generate_minutes)
        lay.addWidget(self.mins_btn)
        return dock

    # ---------- 事件处理 ----------

    def attach_overlay(self, overlay) -> None:
        """app.py 装配后绑定浮窗，使状态切换能同步到浮窗状态栏。"""
        self.overlay = overlay
        self._sync_ov_button()

    def _sync_ov_button(self) -> None:
        """按浮窗当前显隐更新切换按钮文案。"""
        if self.overlay is None:
            return
        self.ov_toggle.setText("🪟 显示浮窗" if self.overlay.isHidden() else "🪟 隐藏浮窗")

    # ---- 自定义标题栏：拖动 / 双击最大化 ----

    def mousePressEvent(self, e) -> None:
        """在自绘标题栏内按下 → 启动整窗拖动。

        优先用合成器原生的 startSystemMove()（Wayland 下最可靠，由 KWin 接管拖动）；
        不可用时回退到手算 move()。
        """
        if e.button() == Qt.MouseButton.LeftButton:
            if self._title_bar is not None and self._title_bar.geometry().contains(e.position().toPoint()):
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
        if self._drag_pos is not None and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag_pos)
            e.accept()

    def mouseReleaseEvent(self, e) -> None:
        self._drag_pos = None

    def mouseDoubleClickEvent(self, e) -> None:
        """双击标题栏 → 最大化 / 还原。"""
        if self._title_bar is not None and self._title_bar.geometry().contains(e.position().toPoint()):
            if self.isMaximized():
                self.showNormal()
            else:
                self.showMaximized()

    # ---- 录音控制（开始 / 暂停 / 停止）----

    def _set_pill(self, text: str, obj: str) -> None:
        self.session_pill.setText(text)
        self.session_pill.setObjectName(obj)
        self.session_pill.style().unpolish(self.session_pill)
        self.session_pill.style().polish(self.session_pill)

    def _on_start_clicked(self) -> None:
        """开始会议：建立会话 + 启动双通道采集。停止后可直接重新开始。"""
        if self._stopped and self.pipeline is not None:
            # 重置 pipeline 以开始新会议
            try:
                self.pipeline.reset()
            except Exception as e:
                self._toast(f"重置失败：{e}")
                return
        self._stopped = False
        self._started = True
        self._t0 = time.monotonic()   # 开始计时（待机时间不计入）
        self._pause_at = None
        self.start_btn.setVisible(False)
        self.play_btn.setEnabled(True)
        self.stop_btn.setEnabled(True)
        self.refine_btn.setEnabled(False)
        self.mins_btn.setEnabled(False)
        self._set_pill("● 录音中", "sessionPillLive")
        if self.overlay is not None:
            self.overlay.set_live()
        if self.pipeline is not None:
            try:
                self.pipeline.start_recording()
            except Exception as e:
                self._toast(f"开始失败：{e}")
        self._toast("会议已开始")

    def _on_pause_toggled(self, checked: bool) -> None:
        """暂停/继续：冻结音频采集（VAD 不切句），UI 状态同步。"""
        self._paused = checked
        if checked:
            # 暂停：把本段时长累加进 _accum，冻结计时
            if self._t0 is not None and self._pause_at is None:
                self._accum += time.monotonic() - self._t0
                self._t0 = None
            self._pause_at = time.monotonic()
        else:
            # 继续：重新开始计时
            if self._t0 is None:
                self._t0 = time.monotonic()
            self._pause_at = None
        if self.pipeline is not None:
            try:
                if checked:
                    self.pipeline.pause()
                else:
                    self.pipeline.resume()
            except Exception as e:
                self._toast(f"{'暂停' if checked else '继续'}失败：{e}")
        if checked:
            self._set_pill("❚❚ 已暂停", "sessionPillPaused")
            if self.overlay is not None:
                self.overlay.set_paused()
            self._toast("已暂停")
        else:
            self._set_pill("● 录音中", "sessionPillLive")
            if self.overlay is not None:
                self.overlay.set_live()
            self._toast("已继续")

    def _on_stop_clicked(self) -> None:
        """停止：结束会话 + 归档；解锁「精修说话人 / 生成会议纪要」+ 可重新开始。"""
        self._stopped = True
        # 停止：结算本段时长，归零以便下次「开始新会议」从头计
        if self._t0 is not None:
            self._accum += time.monotonic() - self._t0
            self._t0 = None
        self._accum = 0.0
        self._pause_at = None
        self.play_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.refine_btn.setEnabled(True)
        self.mins_btn.setEnabled(True)
        # 重新显示开始按钮，允许直接开始新会议
        self.start_btn.setVisible(True)
        self.start_btn.setText("▶ 开始新会议")
        self._set_pill("■ 已停止", "sessionPillStopped")
        if self.overlay is not None:
            self.overlay.set_stopped()
        if self.pipeline is not None:
            try:
                self.pipeline.stop()
            except Exception as e:
                self._toast(f"停止失败：{e}")
        self._toast("已停止 · 会话已归档 · 可开始新会议")

    def _on_refine_speakers(self) -> None:
        """会后离线精修说话人（手动，独立于纪要）。"""
        if self.pipeline is None:
            return
        try:
            res = self.pipeline.refine_speakers()
            n = res.get("n_speakers", 0)
            self.spk_cnt.setText(str(n))
            self._toast(f"精修完成 · {n} 个说话人（labels.json）")
        except Exception as e:
            self._toast(f"精修失败：{e}")

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

    def on_notice(self, text: str) -> None:
        """一次性通知（如未配置翻译 API），停留 6 秒。"""
        self.toast.show()
        self.toast_msg.setText(text)
        self.toast_eta.setText("")
        self.toast.adjustSize()
        self.toast.move(20, 60)
        QTimer.singleShot(6000, self.toast.hide)

    def _make_sub_line(self, p: dict) -> tuple[QFrame, QLabel]:
        """创建一条字幕行（实时 + 完整记录共用）。返回 (line_frame, translation_label)。"""
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
        return line, tr

    def on_asr(self, p: dict) -> None:
        self._sentence_count += 1
        self.sent_cnt.setText(str(self._sentence_count))
        line, tr = self._make_sub_line(p)

        # 实时字幕（trim 15 条）
        self.sub_layout.insertWidget(self.sub_layout.count() - 1, line)
        self._trim_lines()
        self.stage_scroll.verticalScrollBar().setValue(
            self.stage_scroll.verticalScrollBar().maximum())

        # 完整记录（不 trim）
        line2, tr2 = self._make_sub_line(p)
        self.full_layout.insertWidget(self.full_layout.count() - 1, line2)
        self.full_stage_scroll.verticalScrollBar().setValue(
            self.full_stage_scroll.verticalScrollBar().maximum())
        # 存引用供译文回填（两个视图分别跟踪）
        self._line_refs[p["seg_id"]] = tr
        self._full_line_refs.setdefault(p["seg_id"], tr2)
        # 延迟显示
        self.lat_val.setText(f"{p.get('t_first_ms', 0)} ms")

    def on_trans(self, p: dict) -> None:
        tr = self._line_refs.get(p["seg_id"])
        if tr:
            tr.setText(tr.text() + p["delta"])
        tr_full = self._full_line_refs.get(p["seg_id"])
        if tr_full:
            tr_full.setText(tr_full.text() + p["delta"])

    def on_trans_err(self, p: dict) -> None:
        tr = self._line_refs.get(p["seg_id"])
        if tr:
            tr.setText(f"⚠ {p['error']}")
        tr_full = self._full_line_refs.get(p["seg_id"])
        if tr_full:
            tr_full.setText(f"⚠ {p['error']}")

    def on_seg(self, p: dict) -> None:
        pass  # 预留：波形/能量显示

    def _trim_lines(self) -> None:
        while self.sub_layout.count() > 15:
            item = self.sub_layout.takeAt(1)
            if item and item.widget():
                item.widget().deleteLater()

    def _rec_seconds(self) -> int:
        """当前录制时长（秒）：只累计「录音中」的时间，待机/暂停不计。"""
        if self._t0 is None:
            return int(self._accum)
        if self._pause_at is not None:
            return int(self._accum)  # 暂停中：冻结在暂停那一刻
        return int(self._accum + (time.monotonic() - self._t0))

    def _tick(self) -> None:
        s = self._rec_seconds()
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
