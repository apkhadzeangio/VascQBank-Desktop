"""Draft recovery widgets: the per-question bar, the global banner and the draft list."""

from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.services.draft_service import DraftListRow
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.formatting import format_timestamp
from vascqbank_desktop.ui.widgets import make_label


class DraftBar(QFrame):
    """Shown above a question that has an unfinished draft: restore, compare or discard it.

    It never acts by itself — nothing is restored or discarded without a click.
    """

    restoreRequested = Signal()
    compareRequested = Signal()
    discardRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DraftBar")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(8)
        self.text_label = make_label("", "DraftBarText")
        row.addWidget(self.text_label, 1)
        self.restore_button = QPushButton(strings.DR_RESTORE)
        self.restore_button.setObjectName("PrimaryButton")
        self.compare_button = QPushButton(strings.DR_COMPARE)
        self.compare_button.setObjectName("SecondaryButton")
        self.discard_button = QPushButton(strings.DR_DISCARD)
        self.discard_button.setObjectName("DangerButton")
        for button in (self.restore_button, self.compare_button, self.discard_button):
            row.addWidget(button)
        self.restore_button.clicked.connect(self.restoreRequested)
        self.compare_button.clicked.connect(self.compareRequested)
        self.discard_button.clicked.connect(self.discardRequested)
        self.hide()

    def show_draft(self, reviewer: str, saved_at: str, outdated: tuple[int, int] | None) -> None:
        text = strings.DR_BAR_TEXT.format(reviewer=reviewer, time=format_timestamp(saved_at))
        if outdated is not None:
            text += strings.DR_BAR_OUTDATED.format(base=outdated[0], current=outdated[1])
        self.text_label.setText(text)
        self.compare_button.setVisible(outdated is not None)
        self.show()


class DraftBanner(QFrame):
    """A reminder at the top of the catalog while unfinished drafts exist."""

    openListRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("DraftBanner")
        row = QHBoxLayout(self)
        row.setContentsMargins(14, 6, 14, 6)
        self.label = make_label("", "DraftBarText", wrap=False)
        row.addWidget(self.label, 1)
        self.button = QPushButton(strings.DR_BANNER_BUTTON)
        self.button.setObjectName("SecondaryButton")
        self.button.clicked.connect(self.openListRequested)
        row.addWidget(self.button)
        self.hide()

    def set_count(self, count: int) -> None:
        self.label.setText(strings.DR_BANNER.format(count=count))
        self.setVisible(count > 0)


class DraftListDialog(QDialog):
    """Every unfinished draft, including those of earlier sessions and of filtered-out questions."""

    openRequested = Signal(int)  # question id
    compareRequested = Signal(int)
    discardRequested = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.DR_LIST_TITLE)
        self.resize(900, 460)
        self._rows: list[DraftListRow] = []
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(make_label(strings.DR_LIST_TITLE, "PageHeading"))
        layout.addWidget(make_label(strings.DR_LIST_INTRO, "CardBody"))
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            [
                strings.DR_COL_QUESTION,
                strings.DR_COL_SUBJECT,
                strings.DR_COL_REVIEWER,
                strings.DR_COL_SAVED,
                strings.DR_COL_STATE,
            ]
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self.table.horizontalHeader()
        header.setStretchLastSection(True)
        for column in range(4):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._update_buttons)
        self.table.itemDoubleClicked.connect(lambda _item: self._emit(self.openRequested))
        layout.addWidget(self.table, 1)
        self.empty_label = make_label(strings.DR_LIST_EMPTY, "EmptyListMessage")
        self.empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty_label)

        row = QHBoxLayout()
        self.open_button = QPushButton(strings.DR_OPEN)
        self.open_button.setObjectName("PrimaryButton")
        self.compare_button = QPushButton(strings.DR_COMPARE)
        self.compare_button.setObjectName("SecondaryButton")
        self.discard_button = QPushButton(strings.DR_DISCARD)
        self.discard_button.setObjectName("DangerButton")
        self.close_button = QPushButton(strings.DR_CLOSE)
        self.close_button.setObjectName("SecondaryButton")
        for button in (self.open_button, self.compare_button, self.discard_button):
            row.addWidget(button)
        row.addStretch(1)
        row.addWidget(self.close_button)
        layout.addLayout(row)
        self.open_button.clicked.connect(lambda: self._emit(self.openRequested))
        self.compare_button.clicked.connect(lambda: self._emit(self.compareRequested))
        self.discard_button.clicked.connect(lambda: self._emit(self.discardRequested))
        self.close_button.clicked.connect(self.accept)
        self._update_buttons()

    def set_drafts(self, rows: Sequence[DraftListRow]) -> None:
        self._rows = list(rows)
        self.table.setRowCount(len(self._rows))
        for index, draft in enumerate(self._rows):
            state = (
                strings.DR_STATE_OUTDATED.format(
                    base=draft.base_revision_no, current=draft.current_revision_no
                )
                if draft.outdated
                else strings.DR_STATE_CURRENT
            )
            values = (
                draft.uid,
                draft.subject_code,
                draft.reviewer_name,
                format_timestamp(draft.saved_at),
                state,
            )
            for column, text in enumerate(values):
                self.table.setItem(index, column, QTableWidgetItem(text))
        self.table.setVisible(bool(self._rows))
        self.empty_label.setVisible(not self._rows)
        if self._rows:
            self.table.selectRow(0)
        self._update_buttons()

    @property
    def rows(self) -> list[DraftListRow]:
        return list(self._rows)

    def selected_row(self) -> DraftListRow | None:
        index = self.table.currentRow()
        return self._rows[index] if 0 <= index < len(self._rows) else None

    def _emit(self, signal: Signal) -> None:  # type: ignore[valid-type]
        row = self.selected_row()
        if row is not None:
            signal.emit(row.question_id)  # type: ignore[attr-defined]

    def _update_buttons(self) -> None:
        row = self.selected_row()
        self.open_button.setEnabled(row is not None)
        self.discard_button.setEnabled(row is not None)
        self.compare_button.setEnabled(row is not None and row.outdated)
