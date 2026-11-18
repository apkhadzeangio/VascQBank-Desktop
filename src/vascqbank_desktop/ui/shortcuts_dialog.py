"""The keyboard shortcuts at a glance (Help → Keyboard shortcuts, F1)."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QHeaderView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.widgets import make_label


class ShortcutsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.SHORTCUTS_TITLE)
        self.setMinimumSize(640, 520)
        column = QVBoxLayout(self)
        column.setContentsMargins(20, 16, 20, 14)
        column.addWidget(make_label(strings.SHORTCUTS_INTRO, "CardBody"))
        rows = [(group, keys, text) for group, items in strings.SHORTCUTS_GROUPS for keys, text in items]
        self.table = QTableWidget(len(rows), 2)
        self.table.setAccessibleName(strings.SHORTCUTS_TITLE)
        self.table.horizontalHeader().hide()
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setWordWrap(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(True)
        previous_group = None
        for row, (group, keys, text) in enumerate(rows):
            key_item = QTableWidgetItem(keys)
            font = key_item.font()
            font.setBold(True)
            key_item.setFont(font)
            label = f"{group} — {text}" if group != previous_group else text
            previous_group = group
            self.table.setItem(row, 0, key_item)
            self.table.setItem(row, 1, QTableWidgetItem(label))
        self.table.resizeRowsToContents()
        column.addWidget(self.table, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Close).setText(strings.RS_CLOSE)
        column.addWidget(buttons)
        self.table.setFocus(Qt.FocusReason.OtherFocusReason)

    def shortcut_keys(self) -> list[str]:
        return [self.table.item(row, 0).text() for row in range(self.table.rowCount())]
