"""Reviewer profile page: choose or type the name that future edits will be attributed to.

No passwords, accounts or permissions — just a name kept on this computer.
Changing it never rewrites history (that is append-only in the database).
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from vascqbank_desktop.domain.reviewer import (
    MAX_REVIEWER_NAME_LENGTH,
    REVIEWER_PRESETS,
    InvalidReviewerName,
    ReviewerNameProblem,
    normalize_reviewer_name,
)
from vascqbank_desktop.services.settings_service import ReviewerProfile
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.widgets import make_card, make_label, repolish

_PROBLEM_TEXT: dict[ReviewerNameProblem, str] = {
    ReviewerNameProblem.EMPTY: strings.REV_ERR_EMPTY,
    ReviewerNameProblem.TOO_LONG: strings.REV_ERR_TOO_LONG.format(max=MAX_REVIEWER_NAME_LENGTH),
    ReviewerNameProblem.INVALID_CHARACTERS: strings.REV_ERR_CHARACTERS,
}


class ReviewerView(QWidget):
    reviewerChanged = Signal(str)

    def __init__(self, profile: ReviewerProfile, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ContentArea")
        self._profile = profile

        page = QVBoxLayout(self)
        page.setContentsMargins(40, 34, 40, 26)
        page.setSpacing(14)
        page.addWidget(make_label(strings.REV_TITLE, "PageHeading"))
        page.addWidget(make_label(strings.REV_INTRO, "PageTagline"))

        card, layout = make_card()
        layout.addWidget(make_label(strings.REV_CURRENT, "FieldName", wrap=False))
        self.current_label = make_label("", "CurrentReviewer", selectable=True)
        layout.addWidget(self.current_label)
        layout.addSpacing(8)
        layout.addWidget(make_label(strings.REV_NAME_LABEL, "FieldName", wrap=False))

        row = QHBoxLayout()
        self.name_combo = QComboBox()
        self.name_combo.setObjectName("ReviewerCombo")
        self.name_combo.setEditable(True)
        self.name_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        line_edit = self.name_combo.lineEdit()
        line_edit.setPlaceholderText(strings.REV_NAME_PLACEHOLDER)
        line_edit.setMaxLength(MAX_REVIEWER_NAME_LENGTH * 2)  # the exact limit is reported, not silently cut
        line_edit.returnPressed.connect(self.save)
        self.name_combo.currentTextChanged.connect(self._on_text_changed)
        row.addWidget(self.name_combo, 1)
        self.save_button = QPushButton(strings.REV_SAVE)
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.save_button)
        layout.addLayout(row)

        self.message_label = QLabel()
        self.message_label.setObjectName("StatusBad")
        self.message_label.setWordWrap(True)
        self.message_label.hide()
        layout.addWidget(self.message_label)
        layout.addSpacing(6)
        layout.addWidget(make_label(strings.REV_HISTORY_NOTE, "CardBody"))
        page.addWidget(card)
        page.addStretch(1)
        self.refresh()

    # ------------------------------------------------------------------ state
    @property
    def saved_name(self) -> str:
        return self._profile.name

    def refresh(self) -> None:
        """Re-read the profile (name and recently used names)."""
        saved = self._profile.name
        names = list(REVIEWER_PRESETS)
        names += [n for n in self._profile.recent_names() if n not in names]
        self.name_combo.blockSignals(True)
        self.name_combo.clear()
        self.name_combo.addItems(names)
        self.name_combo.setCurrentText(saved)  # blank when nothing has been chosen yet
        self.name_combo.blockSignals(False)
        self.current_label.setText(saved or strings.REV_NONE)
        self._show_message("")
        self._update_save_button()

    def _typed_problem(self) -> ReviewerNameProblem | None:
        try:
            normalize_reviewer_name(self.name_combo.currentText())
        except InvalidReviewerName as exc:
            return exc.problem
        return None

    def _on_text_changed(self, _text: str) -> None:
        problem = self._typed_problem()
        # An empty field is simply not saveable yet; only real mistakes are reported.
        shown = _PROBLEM_TEXT[problem] if problem not in (None, ReviewerNameProblem.EMPTY) else ""
        self._show_message(shown, error=True)
        self._update_save_button()

    def _update_save_button(self) -> None:
        typed = self.name_combo.currentText()
        valid = self._typed_problem() is None
        changed = valid and normalize_reviewer_name(typed) != self._profile.name
        self.save_button.setEnabled(changed)

    def _show_message(self, text: str, *, error: bool = False) -> None:
        self.message_label.setObjectName("StatusBad" if error else "StatusOk")
        repolish(self.message_label)
        self.message_label.setText(text)
        self.message_label.setVisible(bool(text))

    # ------------------------------------------------------------------ saving
    def save(self) -> None:
        if not self.save_button.isEnabled():
            return
        try:
            name = self._profile.set_name(self.name_combo.currentText())
        except InvalidReviewerName as exc:
            self._show_message(_PROBLEM_TEXT[exc.problem], error=True)
            return
        except OSError as exc:
            self._show_message(strings.REV_ERR_SAVE.format(detail=exc), error=True)
            return
        self.refresh()
        self._show_message(strings.REV_SAVED.format(name=name))
        self.reviewerChanged.emit(name)
