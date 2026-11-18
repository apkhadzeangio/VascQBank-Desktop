"""Widgets of the editorial workflow: the action bar and the reason / confirmation dialog.

They only show things and report clicks; the rules (what is allowed, what is written) live in
the domain and services, and :class:`~vascqbank_desktop.ui.catalog_workflow.WorkflowController`
connects them.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.editing import MAX_NOTE_LENGTH
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.workflow import ReviewAction, allowed_actions
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.keys import key
from vascqbank_desktop.ui.widgets import make_label


class WorkflowBar(QFrame):
    """The review actions of the displayed question: only the ones its status allows are shown."""

    approveRequested = Signal()
    approveNextRequested = Signal()
    rejectRequested = Signal()
    reopenRequested = Signal()
    startRequested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("WorkflowBar")
        column = QVBoxLayout(self)
        column.setContentsMargins(10, 8, 10, 8)
        column.setSpacing(6)

        self.approve_next_button = QPushButton(strings.WF_APPROVE_NEXT)
        self.approve_next_button.setObjectName("ApproveButton")
        self.approve_next_button.setToolTip(strings.WF_APPROVE_NEXT_TOOLTIP)
        self.approve_button = QPushButton(strings.WF_APPROVE)
        self.approve_button.setObjectName("ApproveOutlineButton")
        self.approve_button.setToolTip(strings.WF_APPROVE_TOOLTIP)
        self.reject_button = QPushButton(strings.WF_REJECT)
        self.reject_button.setObjectName("DangerButton")
        self.reject_button.setToolTip(strings.WF_REJECT_TOOLTIP)
        self.reopen_button = QPushButton(strings.WF_REOPEN)
        self.reopen_button.setObjectName("SecondaryButton")
        self.reopen_button.setToolTip(strings.WF_REOPEN_TOOLTIP)
        self.start_button = QPushButton(strings.WF_START)
        self.start_button.setObjectName("SecondaryButton")
        self.start_button.setToolTip(strings.WF_START_TOOLTIP)

        first = QHBoxLayout()
        first.setSpacing(8)
        first.addWidget(self.approve_next_button, 1)
        first.addWidget(self.approve_button)
        second = QHBoxLayout()
        second.setSpacing(8)
        second.addWidget(self.reject_button)
        second.addWidget(self.reopen_button)
        second.addWidget(self.start_button)
        second.addStretch(1)
        column.addLayout(first)
        column.addLayout(second)

        self.approve_next_button.clicked.connect(self.approveNextRequested)
        self.approve_button.clicked.connect(self.approveRequested)
        self.reject_button.clicked.connect(self.rejectRequested)
        self.reopen_button.clicked.connect(self.reopenRequested)
        self.start_button.clicked.connect(self.startRequested)
        self._buttons = {
            ReviewAction.APPROVE: (self.approve_next_button, self.approve_button),
            ReviewAction.REJECT: (self.reject_button,),
            ReviewAction.REOPEN: (self.reopen_button,),
            ReviewAction.START_REVIEW: (self.start_button,),
        }
        self.set_status(None)

    def set_status(self, status: ReviewStatus | None) -> None:
        """Show exactly the actions *status* allows (nothing for ``None``)."""
        allowed = set(allowed_actions(status)) if status is not None else set()
        for action, buttons in self._buttons.items():
            for button in buttons:
                button.setVisible(action in allowed)

    def visible_actions(self) -> set[ReviewAction]:
        """The actions currently offered (for tests and shortcuts)."""
        return {action for action, buttons in self._buttons.items() if any(not b.isHidden() for b in buttons)}


class ReasonDialog(QDialog):
    """Ask for a short written reason (required) or note (optional) before a status change."""

    def __init__(
        self,
        title: str,
        intro: str,
        *,
        confirm_text: str,
        required: bool,
        warning: str = "",
        destructive: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setModal(True)
        self.setMinimumWidth(460)
        self._required = required
        column = QVBoxLayout(self)
        column.setContentsMargins(20, 18, 20, 16)
        column.setSpacing(10)
        column.addWidget(make_label(title, "CardTitle", wrap=False))
        column.addWidget(make_label(intro, "CardBody"))
        self.warning_label = make_label(warning, "EditorWarning")
        self.warning_label.setVisible(bool(warning))
        column.addWidget(self.warning_label)
        self.edit = QPlainTextEdit()
        self.edit.setPlaceholderText(strings.WF_REASON_PLACEHOLDER)
        self.edit.setMinimumHeight(90)
        self.edit.setAccessibleName(title)
        self.edit.textChanged.connect(self._clear_error)
        # Phase 9b: keyboard only — Tab moves on to the buttons (a tab character would be collapsed
        # into a space anyway) and Ctrl+Enter confirms from inside the text
        self.edit.setTabChangesFocus(True)
        column.addWidget(self.edit)
        self.error_label = make_label("", "ErrorNotice")
        self.error_label.hide()
        column.addWidget(self.error_label)
        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_button = QPushButton(strings.WF_CANCEL)
        self.cancel_button.setObjectName("SecondaryButton")
        self.cancel_button.clicked.connect(self.reject)
        self.confirm_button = QPushButton(confirm_text)
        self.confirm_button.setObjectName("DangerButton" if destructive else "PrimaryButton")
        self.confirm_button.clicked.connect(self._confirm)
        self.confirm_button.setDefault(False)
        row.addWidget(self.cancel_button)
        row.addWidget(self.confirm_button)
        column.addLayout(row)
        for sequence in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(key(sequence), self, activated=self._confirm)

    def text(self) -> str:
        """The reason as one line (line breaks become spaces), trimmed."""
        return " ".join(self.edit.toPlainText().split())

    def _clear_error(self) -> None:
        self.error_label.hide()

    def _confirm(self) -> None:
        text = self.text()
        if self._required and not text:
            self.error_label.setText(strings.WF_REASON_REQUIRED)
            self.error_label.show()
            self.edit.setFocus()
            return
        if len(text) > MAX_NOTE_LENGTH:
            self.error_label.setText(strings.ED_PROBLEM_NOTE_TOO_LONG.format(max=MAX_NOTE_LENGTH))
            self.error_label.show()
            return
        self.accept()
