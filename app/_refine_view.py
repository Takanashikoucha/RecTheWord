"""Refined-transcript viewer with speaker-name editing (manual refine result).

Shows the offline-diarized, per-speaker transcript with timestamps. Each
anonymous speaker gets an editable name box; saving persists to the session's
``labels.json`` and instantly refreshes the view. Hosts the "Generate Minutes"
entry point (dialog chooses refined vs. live-text-stream source).
"""

import logging
from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger("RecTheWord.RefineView")


class RefineViewDialog(QDialog):
    def __init__(self, parent, session_dir: Path, labels, segments: list,
                 minutes_fn, transcript_export: str = ""):
        """
        :param minutes_fn: fn(source: "refined"|"stream") -> str (markdown)
        :param transcript_export: live text stream (for source B preview/submit)
        """
        super().__init__(parent)
        self.session_dir = Path(session_dir)
        self.labels = labels
        self.segments = segments
        self.minutes_fn = minutes_fn
        self.transcript_export = transcript_export
        self.setWindowTitle("会议精修稿")
        self.resize(760, 560)
        self._build_ui()
        self._refresh_view()

    def _build_ui(self):
        lay = QVBoxLayout(self)

        # Speaker name editors
        names_box = QWidget()
        names_lay = QHBoxLayout(names_box)
        names_lay.setContentsMargins(0, 0, 0, 0)
        spk_ids = sorted({seg.get("spk", 0) for seg in self.segments})
        self._name_edits = {}
        for spk in spk_ids:
            lbl = QLabel(f"说话人{spk}:")
            edit = QLineEdit()
            edit.setPlaceholderText(f"说话人{spk}")
            edit.setFixedWidth(160)
            edit.textChanged.connect(self._on_name_changed)
            names_lay.addWidget(lbl)
            names_lay.addWidget(edit)
            self._name_edits[spk] = edit
        names_lay.addStretch()
        lay.addWidget(names_box)

        # Transcript view
        self._view = QTextEdit()
        self._view.setReadOnly(True)
        lay.addWidget(self._view, 1)

        # Actions
        row = QHBoxLayout()
        btn_gen = QPushButton("生成会议纪要")
        btn_gen.clicked.connect(self._on_generate_minutes)
        btn_export = QPushButton("导出纪要(.md)")
        btn_export.clicked.connect(self._on_export_minutes)
        row.addStretch()
        row.addWidget(btn_export)
        row.addWidget(btn_gen)
        lay.addLayout(row)

    def _on_name_changed(self, _text):
        for spk, edit in self._name_edits.items():
            self.labels.set_name(spk, edit.text())
        self.labels.save(self.session_dir / "labels.json")
        self._refresh_view()

    def _refresh_view(self):
        lines = []
        for seg in self.segments:
            spk = self.labels.name_for(seg.get("spk", 0))
            start = int(seg.get("start_ms", 0)) // 1000
            mm, ss = divmod(start, 60)
            hh, mm = divmod(mm, 60)
            stamp = f"{hh:02d}:{mm:02d}:{ss:02d}"
            lines.append(f"[{stamp}] {spk}: {seg.get('text','').strip()}")
        self._view.setPlainText("\n".join(lines))

    def _on_generate_minutes(self):
        dlg = MinutesSourceDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return
        source = dlg.selected_source()
        try:
            md = self.minutes_fn(source)
        except Exception as e:  # noqa: BLE001
            QMessageBox.critical(self, "生成失败", str(e))
            return
        from app._minutes import save_minutes

        save_minutes(self.session_dir, md)
        QMessageBox.information(
            self, "完成",
            f"纪要已保存到 {self.session_dir / 'minutes.md'}",
        )

    def _on_export_minutes(self):
        p = self.session_dir / "minutes.md"
        if not p.exists():
            QMessageBox.information(self, "提示", "尚未生成纪要，请先点击「生成会议纪要」。")
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "导出纪要", str(p), "Markdown (*.md)"
        )
        if dest:
            import shutil

            shutil.copyfile(str(p), dest)


class MinutesSourceDialog(QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("选择纪要输入源")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("选择用于生成会议纪要的文字来源："))
        self._combo = QComboBox()
        self._combo.addItem("离线精修稿（带说话人分离，需先完成精修）", "refined")
        self._combo.addItem("实时文字流（🎤/🔊 标注，零额外计算）", "stream")
        lay.addWidget(self._combo)
        btn = QPushButton("确定")
        btn.clicked.connect(self.accept)
        lay.addWidget(btn)

    def selected_source(self) -> str:
        return self._combo.currentData()
