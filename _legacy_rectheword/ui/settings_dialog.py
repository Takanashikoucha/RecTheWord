"""Settings dialog: AI endpoint, ASR engine/model, hub, threads, speaker names."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QFormLayout, QGroupBox, QHBoxLayout,
    QSpinBox, QVBoxLayout, QComboBox, QLineEdit, QLabel, QCheckBox,
)
from ..config import AppConfig, AISettings, ASRSettings


class SettingsDialog(QDialog):
    """Collects and applies app settings into an :class:`AppConfig`."""

    def __init__(self, config: AppConfig, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("设置")
        self._config = config
        self.resize(560, 640)
        self._build_ui()
        self._load(config)

    # --------------------------------------------------------------- build
    def _build_ui(self) -> None:
        root = QVBoxLayout(self)

        # ---- AI group
        ai_box = QGroupBox("外部 AI（OpenAI 兼容接口）")
        ai_form = QFormLayout(ai_box)
        self.chk_ai = QCheckBox("启用实时翻译与会议纪要")
        self.edt_ai_url = QLineEdit()
        self.edt_ai_key = QLineEdit()
        self.edt_ai_key.setEchoMode(QLineEdit.Password)
        self.edt_ai_model = QLineEdit()
        self.spin_ai_temp = QSpinBox()
        self.spin_ai_temp.setRange(0, 200)
        self.spin_ai_temp.setSingleStep(10)
        self.spin_ai_temp.setSuffix(" /100")
        self.spin_ai_timeout = QSpinBox()
        self.spin_ai_timeout.setRange(5, 300)
        self.spin_ai_timeout.setSuffix(" 秒")
        ai_form.addRow(self.chk_ai)
        ai_form.addRow("Base URL", self.edt_ai_url)
        ai_form.addRow("API Key", self.edt_ai_key)
        ai_form.addRow("模型", self.edt_ai_model)
        ai_form.addRow("温度", self.spin_ai_temp)
        ai_form.addRow("超时", self.spin_ai_timeout)
        root.addWidget(ai_box)

        # ---- ASR group
        asr_box = QGroupBox("ASR 引擎 / 模型")
        asr_form = QFormLayout(asr_box)
        self.edt_rt_model = QLineEdit()
        self.edt_off_model = QLineEdit()
        self.edt_vad = QLineEdit()
        self.edt_punc = QLineEdit()
        self.edt_spk = QLineEdit()
        self.cmb_hub = QComboBox()
        self.cmb_hub.addItems(["ms (ModelScope)", "hf (HuggingFace)"])
        self.spin_ncpu = QSpinBox()
        self.spin_ncpu.setRange(1, 64)
        self.edt_model_dir = QLineEdit()
        self.spin_preset_spk = QSpinBox()
        self.spin_preset_spk.setRange(0, 16)
        self.spin_preset_spk.setSpecialValueText("自动")
        asr_form.addRow("实时模型", self.edt_rt_model)
        asr_form.addRow("离线模型", self.edt_off_model)
        asr_form.addRow("VAD 模型", self.edt_vad)
        asr_form.addRow("标点模型", self.edt_punc)
        asr_form.addRow("说话人模型", self.edt_spk)
        asr_form.addRow("模型来源 hub", self.cmb_hub)
        asr_form.addRow("CPU 线程数", self.spin_ncpu)
        asr_form.addRow("本地模型目录（可选）", self.edt_model_dir)
        asr_form.addRow("预设说话人数", self.spin_preset_spk)
        root.addWidget(asr_box)

        # ---- Speaker defaults
        spk_box = QGroupBox("说话人默认名称")
        spk_lay = QVBoxLayout(spk_box)
        spk_lay.addWidget(QLabel("未手动命名时，说话人显示为 “<前缀><序号>”。"))
        self.edt_spk_prefix = QLineEdit()
        self.edt_spk_prefix.setPlaceholderText("例如：说话人")
        row = QHBoxLayout()
        row.addWidget(QLabel("名称前缀"))
        row.addWidget(self.edt_spk_prefix)
        spk_lay.addLayout(row)
        root.addWidget(spk_box)

        # ---- buttons
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    # --------------------------------------------------------------- load
    def _load(self, c: AppConfig) -> None:
        self.chk_ai.setChecked(c.ai.enabled)
        self.edt_ai_url.setText(c.ai.base_url)
        self.edt_ai_key.setText(c.ai.api_key)
        self.edt_ai_model.setText(c.ai.model)
        self.spin_ai_temp.setValue(int(round(c.ai.temperature * 100)))
        self.spin_ai_timeout.setValue(c.ai.timeout)
        self.edt_rt_model.setText(c.asr.real_time_model)
        self.edt_off_model.setText(c.asr.offline_model)
        self.edt_vad.setText(c.asr.vad_model)
        self.edt_punc.setText(c.asr.punc_model)
        self.edt_spk.setText(c.asr.spk_model)
        self.cmb_hub.setCurrentIndex(0 if c.asr.hub == "ms" else 1)
        self.spin_ncpu.setValue(c.asr.ncpu)
        self.edt_model_dir.setText(c.asr.model_dir)
        self.spin_preset_spk.setValue(c.asr.preset_spk_num)
        self.edt_spk_prefix.setText(c.default_speaker_names[0]
                                    if c.default_speaker_names else "说话人")

    # -------------------------------------------------------------- apply
    def apply(self) -> AppConfig:
        c = self._config
        ai = AISettings(
            enabled=self.chk_ai.isChecked(),
            base_url=self.edt_ai_url.text().strip(),
            api_key=self.edt_ai_key.text().strip(),
            model=self.edt_ai_model.text().strip(),
            temperature=self.spin_ai_temp.value() / 100.0,
            timeout=self.spin_ai_timeout.value(),
        )
        asr = ASRSettings(
            real_time_model=self.edt_rt_model.text().strip() or "paraformer-zh-streaming",
            offline_model=self.edt_off_model.text().strip() or "iic/SenseVoiceSmall",
            vad_model=self.edt_vad.text().strip(),
            punc_model=self.edt_punc.text().strip(),
            spk_model=self.edt_spk.text().strip(),
            hub="ms" if self.cmb_hub.currentIndex() == 0 else "hf",
            ncpu=self.spin_ncpu.value(),
            model_dir=self.edt_model_dir.text().strip(),
            preset_spk_num=self.spin_preset_spk.value(),
        )
        c.ai = ai
        c.asr = asr
        prefix = self.edt_spk_prefix.text().strip() or "说话人"
        c.default_speaker_names = [prefix, ""]
        return c

    def accept(self) -> None:  # noqa: D102
        self.apply()
        super().accept()
