"""Session management window.

Lists all archived sessions (newest first) with duration/status/integrity
badge. Selecting one offers: open refined transcript (+ speaker renaming),
trigger offline refine, generate minutes, or delete.
"""

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

log = logging.getLogger("RecTheWord.SessionUI")


class SessionWindow(QMainWindow):
    refine_requested = pyqtSignal(str)  # session_id
    minutes_requested = pyqtSignal(str)  # session_id

    def __init__(self, parent, store):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("会议会话管理")
        self.resize(720, 480)
        self._build_ui()
        self.refresh()

    def _build_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        lay = QVBoxLayout(central)

        self._table = QTableWidget(0, 4)
        self._table.setHorizontalHeaderLabels(["开始时间", "时长", "状态", "完整性"])
        self._table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self._table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self._table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        lay.addWidget(self._table, 1)

        row = QHBoxLayout()
        btn_refresh = QPushButton("刷新")
        btn_refresh.clicked.connect(self.refresh)
        btn_refine = QPushButton("离线精修")
        btn_refine.clicked.connect(self._on_refine)
        btn_minutes = QPushButton("生成纪要")
        btn_minutes.clicked.connect(self._on_minutes)
        btn_delete = QPushButton("删除会话")
        btn_delete.clicked.connect(self._on_delete)
        row.addStretch()
        row.addWidget(btn_refresh)
        row.addWidget(btn_refine)
        row.addWidget(btn_minutes)
        row.addWidget(btn_delete)
        lay.addLayout(row)

    def refresh(self):
        sessions = self.store.list_sessions()
        self._table.setRowCount(len(sessions))
        for r, e in enumerate(sessions):
            dur = e.get("duration_s", 0)
            mm, ss = divmod(int(dur), 60)
            integ = self.store.integrity(e.get("id", ""))
            badges = []
            if not integ.get("mix_wav"):
                badges.append("缺wav")
            if not integ.get("transcript"):
                badges.append("缺文字流")
            if integ.get("refined"):
                badges.append("已精修")
            if integ.get("minutes"):
                badges.append("有纪要")
            values = [
                e.get("start", ""),
                f"{mm}分{ss}秒",
                e.get("status", ""),
                " ".join(badges) or "-",
            ]
            for c, v in enumerate(values):
                item = QTableWidgetItem(str(v))
                if c == 0:
                    item.setData(Qt.ItemDataRole.UserRole, e.get("id"))
                self._table.setItem(r, c, item)

    def _selected_id(self) -> str:
        row = self._table.currentRow()
        if row < 0:
            return ""
        item = self._table.item(row, 0)
        return item.data(Qt.ItemDataRole.UserRole) if item else ""

    def _require_selection(self) -> str:
        sid = self._selected_id()
        if not sid:
            QMessageBox.information(self, "提示", "请先选择一个会话。")
        return sid

    def _on_refine(self):
        sid = self._require_selection()
        if sid:
            self.refine_requested.emit(sid)

    def _on_minutes(self):
        sid = self._require_selection()
        if sid:
            self.minutes_requested.emit(sid)

    def _on_delete(self):
        sid = self._require_selection()
        if not sid:
            return
        if QMessageBox.question(
            self, "确认删除", f"确定删除会话 {sid} 及其全部文件？"
        ) != QMessageBox.StandardButton.Yes:
            return
        self.store.delete(sid)
        self.refresh()
