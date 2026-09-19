"""设置对话框：常用设置可视化 + 持久化 + 热生效/需重启分类。

设计要点（方案 Part A）：
- 分组：语言 / 翻译 API / 字幕外观 / VAD 灵敏度 / 说话人分离 / 高级（只读提示）。
- 从当前 cfg 取值初始化；「保存」emit settings_saved(proposed_cfg, hot, restart)。
- 「测试连接」用 LlmApiClient.health() 探活，toast 反馈成功/失败。
- 高级项（ASR/音频/backlog/会话目录）只读展示 + 标注「需重启生效」，不在面板改。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QLineEdit, QPushButton, QScrollArea, QSpinBox,
                               QVBoxLayout, QWidget)

from ..core.config import Config, classify_changes
from .theme import MAIN_QSS

LANGS = ["自动", "中文", "English", "日本語", "한국어", "Deutsch"]
_LANG_MAP = {"自动": "", "中文": "zh", "English": "en", "日本語": "ja",
             "한국어": "ko", "Deutsch": "de"}
_LANG_REV = {v: k for k, v in _LANG_MAP.items()}
TARGET_LANGS = ["中文", "English", "日本語", "한국어", "Deutsch"]
THEMES = ["light", "dark"]


def _lang_combo(options: list[str], cur: str) -> tuple[QComboBox, dict]:
    cb = QComboBox()
    cb.addItems(options)
    rev = {v: k for k, v in (_LANG_MAP if options == LANGS else
                            {k: k for k in TARGET_LANGS}).items()}
    # 用反向映射定位当前项
    idx = 0
    for i, opt in enumerate(options):
        code = _LANG_MAP.get(opt, opt)
        if code == cur:
            idx = i
            break
    cb.setCurrentIndex(idx)
    return cb, {opt: _LANG_MAP.get(opt, opt) for opt in options}


class SettingsDialog(QDialog):
    """设置面板。保存后 emit settings_saved(proposed, hot_paths, restart_paths)。"""

    settings_saved = Signal(object, object, object)

    def __init__(self, cfg: Config, parent=None,
                 pipeline=None, api=None, bus=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.pipeline = pipeline
        self.api = api
        self.bus = bus
        self.setWindowTitle("设置 — RecTheWord")
        self.resize(560, 620)
        self.setStyleSheet(MAIN_QSS)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(12)

        # 生效来源徽章
        src = QLabel("当前生效来源：用户设置（~/.rectheword/settings.yaml）"
                     if self._has_user_settings() else
                     "当前生效来源：config.yaml（仓库默认）")
        src.setObjectName("fieldLbl")
        outer.addWidget(src)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 0, 0)
        bl.setSpacing(14)

        bl.addWidget(self._group_language())
        bl.addWidget(self._group_api())
        bl.addWidget(self._group_appearance())
        bl.addWidget(self._group_vad())
        bl.addWidget(self._group_diarization())
        bl.addWidget(self._group_advanced())
        bl.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # 底部按钮
        foot = QHBoxLayout()
        foot.addStretch(1)
        cancel = QPushButton("取消")
        cancel.clicked.connect(self.reject)
        foot.addWidget(cancel)
        save = QPushButton("保存")
        save.setObjectName("primaryBtn")
        save.clicked.connect(self._on_save)
        foot.addWidget(save)
        outer.addLayout(foot)

    # ---- 分组构建 ----

    def _group_language(self) -> QGroupBox:
        g = QGroupBox("语言")
        f = QFormLayout(g)
        self.src_cb, self.src_map = _lang_combo(LANGS, self.cfg.ui.source_lang)
        self.tgt_cb, self.tgt_map = _lang_combo(TARGET_LANGS, self.cfg.ui.target_lang)
        f.addRow("源语言", self.src_cb)
        f.addRow("目标语言", self.tgt_cb)
        note = QLabel("源语言「自动」= 信任 ASR 每段检测；显式选择则覆盖。")
        note.setObjectName("laneSub")
        f.addRow(note)
        return g

    def _group_api(self) -> QGroupBox:
        g = QGroupBox("翻译 API（OpenAI 兼容）")
        f = QFormLayout(g)
        self.api_url = QLineEdit(self.cfg.api.base_url)
        self.api_key = QLineEdit(self.cfg.api.api_key)
        self.api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_model = QLineEdit(self.cfg.api.model)
        self.api_tr = QSpinBox(); self.api_tr.setRange(3, 120)
        self.api_tr.setValue(int(self.cfg.api.translate_timeout_s))
        self.api_mi = QSpinBox(); self.api_mi.setRange(10, 600)
        self.api_mi.setValue(int(self.cfg.api.minutes_timeout_s))
        f.addRow("Base URL", self.api_url)
        f.addRow("API Key", self.api_key)
        f.addRow("Model", self.api_model)
        f.addRow("翻译超时(s)", self.api_tr)
        f.addRow("纪要超时(s)", self.api_mi)
        row = QHBoxLayout()
        test = QPushButton("测试连接")
        test.clicked.connect(self._on_test_connection)
        row.addWidget(test)
        self.conn_lbl = QLabel("")
        self.conn_lbl.setObjectName("laneSub")
        row.addWidget(self.conn_lbl)
        row.addStretch(1)
        f.addRow(row)
        return g

    def _group_appearance(self) -> QGroupBox:
        g = QGroupBox("字幕外观")
        f = QFormLayout(g)
        self.theme_cb = QComboBox(); self.theme_cb.addItems(THEMES)
        self.theme_cb.setCurrentText(self.cfg.ui.subtitle_theme)
        self.ov_theme_cb = QComboBox()
        self.ov_theme_cb.addItems(["glass", "solid", "outline", "light"])
        self.ov_theme_cb.setCurrentText(self.cfg.ui.overlay_theme)
        self.font_spin = QSpinBox(); self.font_spin.setRange(16, 48)
        self.font_spin.setValue(self.cfg.ui.font_size)
        self.show_orig = QCheckBox("显示原文")
        self.show_orig.setChecked(self.cfg.ui.show_original)
        f.addRow("主窗外观", self.theme_cb)
        f.addRow("浮窗主题", self.ov_theme_cb)
        f.addRow("字号(px)", self.font_spin)
        f.addRow(self.show_orig)
        return g

    def _group_vad(self) -> QGroupBox:
        g = QGroupBox("VAD 灵敏度")
        f = QFormLayout(g)
        self.vad_thr = QDoubleSpinBox(); self.vad_thr.setDecimals(2)
        self.vad_thr.setSingleStep(0.05); self.vad_thr.setRange(0.0, 1.0)
        self.vad_thr.setValue(float(self.cfg.vad.threshold))
        self.vad_sil = QSpinBox(); self.vad_sil.setSuffix(" ms")
        self.vad_sil.setRange(100, 3000); self.vad_sil.setValue(
            self.cfg.vad.min_silence_ms)
        f.addRow("阈值", self.vad_thr)
        f.addRow("最短静音", self.vad_sil)
        note = QLabel("越高越不敏感（更少起句）；最短静音越长越晚断句。")
        note.setObjectName("laneSub")
        f.addRow(note)
        return g

    def _group_diarization(self) -> QGroupBox:
        g = QGroupBox("说话人分离")
        f = QFormLayout(g)
        self.sep_chk = QCheckBox("会后离线聚类标注")
        # 初值取自 pipeline.diarizer（无 pipeline 时默认开）
        init = True
        if self.pipeline is not None:
            init = bool(getattr(self.pipeline.diarizer, "enabled", True))
        self.sep_chk.setChecked(init)
        f.addRow(self.sep_chk)
        return g

    def _group_advanced(self) -> QGroupBox:
        g = QGroupBox("高级（需重启生效）")
        f = QFormLayout(g)
        a = self.cfg.asr
        info = QLabel(f"ASR：{a.model} · {a.device} · {a.compute_type} · "
                     f"{a.threads} 线程 · {a.segment_policy}")
        info.setObjectName("laneSub")
        f.addRow(info)
        b = self.cfg.backlog
        binfo = QLabel(f"积压自适应：{'开' if b.enabled else '关'} · "
                      f"高水位 {b.high_watermark} / 低水位 {b.low_watermark} / "
                      f"上限 {b.maxsize}")
        binfo.setObjectName("laneSub")
        f.addRow(binfo)
        s = QLabel(f"会话目录：{self.cfg.session.dir}")
        s.setObjectName("laneSub")
        f.addRow(s)
        warn = QLabel("以上项修改后需重启程序生效（可在 config.yaml 中调整）。")
        warn.setObjectName("laneSub")
        f.addRow(warn)
        return g

    # ---- 行为 ----

    def _has_user_settings(self) -> bool:
        from ..core.config import USER_SETTINGS_FILE
        return USER_SETTINGS_FILE.exists()

    def _on_test_connection(self) -> None:
        """用候选 API 配置探活（不改动当前运行的 api 客户端）。"""
        from ..llm_api.client import LlmApiClient
        url = self.api_url.text().strip()
        if not url:
            self.conn_lbl.setText("✗ 未填写 Base URL")
            return
        probe = LlmApiClient(url, self.api_key.text().strip(),
                            self.api_model.text().strip())
        self.conn_lbl.setText("… 连接中")
        ok = probe.health()
        self.conn_lbl.setText(
            f"✓ 连接成功（{probe.model or '默认模型'}）" if ok
            else f"✗ 连接失败：{probe.last_error or '不可达'}")

    def _build_proposed(self) -> Config:
        """从控件取值构造拟议 Config（不改动 self.cfg）。"""
        from dataclasses import replace
        prop = Config()
        # 复制原始各 section 再覆盖面板项
        for sec in ("audio", "vad", "asr", "api", "ui", "session", "backlog"):
            setattr(prop, sec, replace(getattr(self.cfg, sec)))
        prop.ui.source_lang = self.src_map[self.src_cb.currentText()]
        prop.ui.target_lang = self.tgt_map[self.tgt_cb.currentText()]
        prop.ui.subtitle_theme = self.theme_cb.currentText()
        prop.ui.overlay_theme = self.ov_theme_cb.currentText()
        prop.ui.font_size = self.font_spin.value()
        prop.ui.show_original = self.show_orig.isChecked()
        prop.api.base_url = self.api_url.text().strip()
        prop.api.api_key = self.api_key.text().strip()
        prop.api.model = self.api_model.text().strip()
        prop.api.translate_timeout_s = float(self.api_tr.value())
        prop.api.minutes_timeout_s = float(self.api_mi.value())
        prop.vad.threshold = float(self.vad_thr.value())
        prop.vad.min_silence_ms = int(self.vad_sil.value())
        return prop

    def diarization_enabled(self) -> bool:
        """说话人分离开关（不在 Config 里，单独暴露给调用方热应用）。"""
        return self.sep_chk.isChecked()

    def _on_save(self) -> None:
        prop = self._build_proposed()
        hot, restart = classify_changes(self.cfg, prop)
        self.settings_saved.emit(prop, hot, restart)
        self.accept()
