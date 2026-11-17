"""Read-only revision history of one question.

Lists every revision (newest first) with its editor, time, note and status change, shows the
full text of the selected one and can compare it with the current version. Revisions are
append-only and never modified here; restoring an old revision is deferred to a later phase.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QScrollArea,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.catalog import RevisionDetail, RevisionInfo
from vascqbank_desktop.domain.textdiff import compare_contents
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.diff_view import DiffView
from vascqbank_desktop.ui.formatting import content_plain_text, format_timestamp
from vascqbank_desktop.ui.widgets import SelectableText, make_label

log = logging.getLogger(__name__)

_ORIGINS = {
    "IMPORT": strings.HI_ORIGIN_IMPORT,
    "EDIT": strings.HI_ORIGIN_EDIT,
    "RESTORE": strings.HI_ORIGIN_RESTORE,
}


def revision_row_text(info: RevisionInfo) -> str:
    """Two lines: who/when, then origin, note and flags."""
    first = strings.HI_ROW.format(
        number=info.revision_no, author=info.author, time=format_timestamp(info.created_at)
    )
    flags = [_ORIGINS.get(info.origin, info.origin)]
    if info.is_current:
        flags.append(strings.HI_CURRENT)
    if info.was_approved:
        flags.append(strings.HI_APPROVED_MARK)
    if info.note:
        flags.append(f"„{info.note}“")
    return f"{first}\n{' · '.join(flags)}"


class RevisionHistoryView(QWidget):
    backRequested = Signal()

    def __init__(self, service: CatalogService, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._service = service
        self._question_id: int | None = None
        self._revisions: list[RevisionInfo] = []
        self._current: RevisionDetail | None = None
        self._selected: RevisionDetail | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(8)
        top = QHBoxLayout()
        self.back_button = QPushButton(strings.HI_BACK)
        self.back_button.setObjectName("NavButton")
        self.back_button.clicked.connect(self.backRequested)
        self.title_label = make_label(strings.HI_TITLE, "CardTitle", wrap=False)
        top.addWidget(self.back_button)
        top.addWidget(self.title_label, 1)
        root.addLayout(top)

        # The detail area is narrow, so the list sits above the text instead of beside it.
        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        self.list = QListWidget()
        self.list.setObjectName("RevisionList")
        self.list.setMinimumHeight(110)
        self.list.setWordWrap(True)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.currentRowChanged.connect(self._on_row)
        splitter.addWidget(self.list)
        splitter.addWidget(self._build_detail())
        splitter.setSizes([170, 480])
        root.addWidget(splitter, 1)
        self.note_label = make_label(strings.HI_READ_ONLY, "ReadOnlyNote")
        root.addWidget(self.note_label)

    def _build_detail(self) -> QWidget:
        panel = QWidget()
        column = QVBoxLayout(panel)
        column.setContentsMargins(0, 8, 0, 0)
        column.setSpacing(8)

        meta = QFrame()
        meta.setObjectName("Card")
        grid = QGridLayout(meta)
        grid.setContentsMargins(16, 12, 16, 12)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)
        grid.setColumnStretch(1, 1)
        self.meta: dict[str, SelectableText] = {}
        for row, (key, caption) in enumerate(
            (
                ("revision", strings.DET_REVISION_FIELD),
                ("author", strings.HI_META_AUTHOR),
                ("time", strings.HI_META_TIME),
                ("origin", strings.HI_META_ORIGIN),
                ("status", strings.HI_META_STATUS),
                ("note", strings.HI_META_NOTE),
            )
        ):
            grid.addWidget(make_label(caption, "FieldName", wrap=False), row, 0, Qt.AlignmentFlag.AlignTop)
            value = SelectableText("FieldValue")
            self.meta[key] = value
            grid.addWidget(value, row, 1)
        column.addWidget(meta)

        self.diff_check = QCheckBox(strings.HI_SHOW_DIFF)
        self.diff_check.setChecked(True)
        self.diff_check.toggled.connect(self._refresh_body)
        column.addWidget(self.diff_check)

        self.stack = QStackedWidget()
        self.text_scroll = QScrollArea()
        self.text_scroll.setObjectName("DetailScroll")
        self.text_scroll.setWidgetResizable(True)
        self.text_scroll.setFrameShape(QFrame.Shape.NoFrame)
        holder = QWidget()
        holder.setObjectName("DetailBody")
        holder_layout = QVBoxLayout(holder)
        holder_layout.setContentsMargins(0, 0, 8, 8)
        self.text_label = SelectableText("ExplanationText")
        holder_layout.addWidget(self.text_label)
        holder_layout.addStretch(1)
        self.text_scroll.setWidget(holder)
        self.diff_view = DiffView()
        self.stack.addWidget(self.text_scroll)
        self.stack.addWidget(self.diff_view)
        column.addWidget(self.stack, 1)
        return panel

    # ------------------------------------------------------------------ content
    @property
    def question_id(self) -> int | None:
        return self._question_id

    @property
    def revisions(self) -> list[RevisionInfo]:
        return list(self._revisions)

    @property
    def selected_revision(self) -> RevisionDetail | None:
        return self._selected

    def show_history(self, question_id: int, uid: str) -> None:
        """Load the history of a question (metadata only; the text is read per selection)."""
        self._question_id = question_id
        self.title_label.setText(f"{strings.HI_TITLE} — {uid}")
        self._revisions = self._service.revisions(question_id)
        current = next((r for r in self._revisions if r.is_current), None)
        self._current = self._service.revision(current.id) if current else None
        self.list.blockSignals(True)
        self.list.clear()
        for info in self._revisions:
            item = QListWidgetItem(revision_row_text(info))
            item.setData(Qt.ItemDataRole.UserRole, info.id)
            self.list.addItem(item)
        self.list.blockSignals(False)
        if self._revisions:
            self.list.setCurrentRow(0)
            self._on_row(0)

    def select_revision_number(self, revision_no: int) -> None:
        for row, info in enumerate(self._revisions):
            if info.revision_no == revision_no:
                self.list.setCurrentRow(row)
                return

    def _on_row(self, row: int) -> None:
        if not 0 <= row < len(self._revisions):
            return
        detail = self._service.revision(self._revisions[row].id)
        self._selected = detail
        if detail is None:
            return
        info = detail.info
        status = (
            strings.HI_STATUS_CHANGE.format(before=info.from_status.label_ka, after=info.to_status.label_ka)
            if info.from_status and info.to_status
            else "—"
        )
        values = {
            "revision": str(info.revision_no) + (f" ({strings.HI_CURRENT})" if info.is_current else ""),
            "author": info.author,
            "time": format_timestamp(info.created_at),
            "origin": _ORIGINS.get(info.origin, info.origin),
            "status": status + (f" · {strings.HI_APPROVED_MARK}" if info.was_approved else ""),
            "note": info.note or strings.HI_NO_NOTE,
        }
        for key, text in values.items():
            self.meta[key].set_content(text)
        self.diff_check.setEnabled(not info.is_current)
        self._refresh_body()

    def _refresh_body(self) -> None:
        detail, current = self._selected, self._current
        if detail is None:
            return
        comparing = self.diff_check.isChecked() and not detail.info.is_current and current is not None
        if comparing and current is not None:
            self.diff_view.set_comparison(
                compare_contents(detail.content, current.content),
                title=f"{strings.HI_SHOW_DIFF}: {detail.info.revision_no} → {current.info.revision_no}",
            )
            self.stack.setCurrentWidget(self.diff_view)
        else:
            self.text_label.set_content(content_plain_text(detail.content))
            self.stack.setCurrentWidget(self.text_scroll)
