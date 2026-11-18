"""The question editor (edit mode of the detail area).

Data-safety rules implemented here (each has tests):

* Typing never touches saved history. After ≈1 s without typing the text is written as a
  **draft** (see :mod:`vascqbank_desktop.ui.autosave`); a status changes only when a revision is
  explicitly saved.
* The state line is honest: "draft saved" appears only after the draft transaction committed,
  "saved — revision N" only after the revision transaction committed. A failed write shows an
  error, keeps the text in the fields and retries.
* Leaving a question flushes the draft first and refuses to leave (or asks) if that fails.
* Saving an approved question asks first: the new revision revokes the approval.
* If the question was saved elsewhere meanwhile, nothing is overwritten: the reviewer chooses
  between saving over the newer revision knowingly (it stays in the history), comparing, or
  keeping the text as a draft.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from enum import StrEnum

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.answers import ANSWER_LETTERS, AnswerLetter
from vascqbank_desktop.domain.editing import (
    MAX_FIELD_LENGTH,
    MAX_NOTE_LENGTH,
    EditField,
    FieldProblem,
    ProblemKind,
    normalize_content,
    same_content,
    validate_content,
)
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.draft_service import DiscardReason, DraftRecord, DraftService
from vascqbank_desktop.services.edit_errors import (
    EditConflict,
    EditTargetMissing,
    ReviewerRequired,
    StorageFailure,
    StorageProblem,
    ValidationFailed,
)
from vascqbank_desktop.services.edit_service import EditService, SaveResult
from vascqbank_desktop.services.settings_service import ReviewerProfile
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.autosave import (
    DEFAULT_DELAY_MS,
    DEFAULT_RETRY_MS,
    DraftAutosaver,
    DraftSnapshot,
    DraftWrite,
)
from vascqbank_desktop.ui.formatting import format_clock
from vascqbank_desktop.ui.keys import key
from vascqbank_desktop.ui.text_edit import AutoGrowTextEdit
from vascqbank_desktop.ui.widgets import make_label, repolish

log = logging.getLogger(__name__)


class EditorState(StrEnum):
    CLEAN = "CLEAN"  # the fields show the saved text
    UNSAVED = "UNSAVED"  # typed; the draft write has not happened yet
    SAVING_DRAFT = "SAVING_DRAFT"
    DRAFT_SAVED = "DRAFT_SAVED"  # the draft is committed; no revision has been saved
    SAVING = "SAVING"  # the revision transaction is running
    SAVED = "SAVED"  # a new revision is committed
    ERROR = "ERROR"
    CONFLICT = "CONFLICT"


class CancelChoice(StrEnum):
    KEEP_DRAFT = "KEEP_DRAFT"
    DISCARD = "DISCARD"
    CONTINUE = "CONTINUE"


@dataclass(frozen=True, slots=True)
class EditSession:
    """Everything the editor needs to know about the question it was opened on."""

    question_id: int
    uid: str
    subject_name: str
    base_revision_id: int
    base_revision_no: int
    base_lock_version: int
    base_content: QuestionContent  # the saved text the edits are measured against
    status: ReviewStatus  # the question's current status (for the approval warning)
    current_revision_no: int
    initial_content: QuestionContent  # what the fields show at the start (saved text or draft)
    initial_note: str = ""
    restored_draft: DraftRecord | None = None


class EditOptionRow(QFrame):
    """One editable answer: letter badge, text field and a "this is the correct answer" radio."""

    def __init__(self, letter: str, shortcut_hint: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EditOptionCard")
        self.letter = letter
        self.setProperty("correct", False)
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)
        self.badge = QLabel(letter)
        self.badge.setObjectName("OptionLetter")
        self.badge.setFixedSize(30, 30)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        self.edit = AutoGrowTextEdit(min_lines=2, max_lines=14)
        self.edit.setAccessibleName(strings.ED_FIELD_OPTION.format(letter=letter))
        row.addWidget(self.edit, 1)
        self.radio = QRadioButton(strings.DET_CORRECT_TAG.removeprefix("✓ "))
        self.radio.setToolTip(f"{strings.DET_CORRECT_TAG} ({shortcut_hint})")
        self.radio.setFocusPolicy(Qt.FocusPolicy.ClickFocus)  # Tab moves through the text fields
        row.addWidget(self.radio, 0, Qt.AlignmentFlag.AlignTop)

    def set_correct(self, correct: bool) -> None:
        self.setProperty("correct", correct)
        repolish(self)
        repolish(self.badge)

    @property
    def is_correct(self) -> bool:
        return bool(self.property("correct"))


def storage_text(failure: StorageFailure) -> str:
    base = {
        StorageProblem.DISK_FULL: strings.ED_STORAGE_DISK_FULL,
        StorageProblem.LOCKED: strings.ED_STORAGE_LOCKED,
        StorageProblem.READ_ONLY: strings.ED_STORAGE_READ_ONLY,
        StorageProblem.IO_ERROR: strings.ED_STORAGE_IO,
        StorageProblem.OTHER: strings.ED_STORAGE_OTHER,
    }[failure.problem]
    return base + "."


def problem_text(problem: FieldProblem) -> str:
    """One validation problem in Georgian."""
    if problem.kind is ProblemKind.NOTE_TOO_LONG:
        return strings.ED_PROBLEM_NOTE_TOO_LONG.format(max=MAX_NOTE_LENGTH)
    field = problem.field
    if field is EditField.STEM:
        name = strings.ED_FIELD_STEM
    elif field is EditField.EXPLANATION:
        name = strings.ED_FIELD_EXPLANATION
    else:
        name = strings.ED_FIELD_OPTION.format(letter=field.option_letter if field else "")
    template = {
        ProblemKind.EMPTY: strings.ED_PROBLEM_EMPTY,
        ProblemKind.CONTROL_CHARACTERS: strings.ED_PROBLEM_CONTROL,
        ProblemKind.TOO_LONG: strings.ED_PROBLEM_TOO_LONG,
    }[problem.kind]
    return template.format(field=name, max=MAX_FIELD_LENGTH)


class QuestionEditorView(QWidget):
    stateChanged = Signal(object)  # EditorState
    saved = Signal(object)  # SaveResult (also when nothing changed)
    draftChanged = Signal()  # a draft was written, replaced or removed
    finished = Signal()  # edit mode ended (cancel, conflict "keep as draft", …)
    compareRequested = Signal(object, object)  # EditSession, the text currently in the fields
    reviewerRequired = Signal()

    def __init__(
        self,
        edit_service: EditService,
        draft_service: DraftService,
        profile: ReviewerProfile,
        parent: QWidget | None = None,
        *,
        autosave_delay_ms: int = DEFAULT_DELAY_MS,
        autosave_retry_ms: int = DEFAULT_RETRY_MS,
    ) -> None:
        super().__init__(parent)
        self._edit_service = edit_service
        self._drafts = draft_service
        self._profile = profile
        self._session: EditSession | None = None
        self._loading = False
        self._seq = 0
        self._draft_exists = False
        self._draft_saved_at: str | None = None
        self._busy = False
        self._conflict: EditConflict | None = None
        self._flash: EditorState | None = None  # SAVED / unchanged message until the next keystroke
        self._flash_text = ""
        self._last_save_error: StorageFailure | None = None

        self._autosave = DraftAutosaver(
            draft_service, self, delay_ms=autosave_delay_ms, retry_ms=autosave_retry_ms
        )
        self._autosave.draftSaved.connect(self._on_draft_saved)
        self._autosave.writeFailed.connect(self._on_write_failed)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(8)
        outer.addWidget(self._build_scroll(), 1)
        outer.addWidget(self._build_footer())
        self._build_shortcuts()
        self._set_tab_order()

    # ------------------------------------------------------------------ construction
    def _build_scroll(self) -> QScrollArea:
        self.scroll = QScrollArea()
        self.scroll.setObjectName("DetailScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("DetailBody")
        self.scroll.setWidget(body)
        page = QVBoxLayout(body)
        page.setContentsMargins(4, 2, 12, 16)
        page.setSpacing(12)

        self.header_label = make_label("", "DetailUid", wrap=False)
        page.addWidget(self.header_label)
        self.warning_label = make_label(strings.ED_APPROVED_WARNING, "EditorWarning")
        self.info_label = make_label("", "EditorInfo")
        page.addWidget(self.warning_label)
        page.addWidget(self.info_label)
        page.addWidget(self._build_conflict_panel())

        page.addWidget(self._section(strings.ED_SECTION_STEM, self._make_stem()))
        page.addWidget(self._section(strings.ED_SECTION_OPTIONS, self._make_options()))
        page.addWidget(self._section(strings.ED_SECTION_EXPLANATION, self._make_explanation()))
        self.note_edit = QLineEdit()
        self.note_edit.setObjectName("EditorNote")
        self.note_edit.setPlaceholderText(strings.ED_NOTE_PLACEHOLDER)
        self.note_edit.setMaxLength(MAX_NOTE_LENGTH)
        self.note_edit.setAccessibleName(strings.ED_FIELD_NOTE)
        self.note_edit.textChanged.connect(self._on_changed)
        page.addWidget(self._section(strings.ED_SECTION_NOTE, self.note_edit))
        page.addStretch(1)
        return self.scroll

    def _section(self, title: str, content: QWidget) -> QFrame:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 14, 18, 16)
        layout.setSpacing(8)
        layout.addWidget(make_label(title, "SectionTitle", wrap=False))
        layout.addWidget(content)
        return frame

    def _make_stem(self) -> QWidget:
        self.stem_edit = AutoGrowTextEdit(min_lines=3, max_lines=30)
        self.stem_edit.setProperty("stem", True)
        self.stem_edit.setAccessibleName(strings.ED_FIELD_STEM)
        self.stem_edit.textChanged.connect(self._on_changed)
        return self.stem_edit

    def _make_options(self) -> QWidget:
        holder = QWidget()
        column = QVBoxLayout(holder)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(8)
        self.option_rows = [
            EditOptionRow(letter, f"Ctrl+{number}") for number, letter in enumerate(ANSWER_LETTERS, start=1)
        ]
        self._correct_group = QButtonGroup(self)  # exactly one radio can be on
        self._correct_group.setExclusive(True)
        for index, row in enumerate(self.option_rows):
            column.addWidget(row)
            self._correct_group.addButton(row.radio, index)
            row.edit.textChanged.connect(self._on_changed)
        self._correct_group.idToggled.connect(self._on_correct_toggled)
        return holder

    def _make_explanation(self) -> QWidget:
        self.explanation_edit = AutoGrowTextEdit(min_lines=4, max_lines=40)
        self.explanation_edit.setAccessibleName(strings.ED_FIELD_EXPLANATION)
        self.explanation_edit.textChanged.connect(self._on_changed)
        return self.explanation_edit

    def _build_conflict_panel(self) -> QFrame:
        self.conflict_panel = QFrame()
        self.conflict_panel.setObjectName("ConflictPanel")
        layout = QVBoxLayout(self.conflict_panel)
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(8)
        self.conflict_label = make_label("", "CardBody")
        layout.addWidget(self.conflict_label)
        stack = QVBoxLayout()  # one button per row: Georgian captions are long and must never overflow
        stack.setSpacing(6)
        self.conflict_mine_button = QPushButton(strings.ED_CONFLICT_MINE)
        self.conflict_mine_button.setObjectName("DangerButton")
        self.conflict_mine_button.setToolTip(strings.ED_CONFLICT_MINE_TOOLTIP)
        self.conflict_compare_button = QPushButton(strings.ED_CONFLICT_COMPARE)
        self.conflict_compare_button.setObjectName("SecondaryButton")
        self.conflict_keep_button = QPushButton(strings.ED_CONFLICT_KEEP)
        self.conflict_keep_button.setObjectName("SecondaryButton")
        for button in (self.conflict_compare_button, self.conflict_keep_button, self.conflict_mine_button):
            stack.addWidget(button)
        layout.addLayout(stack)
        self.conflict_mine_button.clicked.connect(self._save_over_newer)
        self.conflict_compare_button.clicked.connect(self._request_compare)
        self.conflict_keep_button.clicked.connect(self._keep_draft_and_leave)
        self.conflict_panel.hide()
        return self.conflict_panel

    def _build_footer(self) -> QFrame:
        footer = QFrame()
        footer.setObjectName("EditorFooter")
        column = QVBoxLayout(footer)
        column.setContentsMargins(14, 10, 14, 10)
        column.setSpacing(6)
        self.validation_label = make_label("", "ValidationList")
        self.validation_label.hide()
        column.addWidget(self.validation_label)
        self.state_label = make_label("", "EditorState")
        self.state_label.setProperty("state", EditorState.CLEAN.value)
        column.addWidget(self.state_label)  # full-width row: the state text must stay readable
        row = QHBoxLayout()
        row.setSpacing(8)
        self.revert_button = QPushButton(strings.ED_REVERT)
        self.revert_button.setObjectName("SecondaryButton")
        self.revert_button.setToolTip(strings.ED_REVERT_TOOLTIP)
        self.revert_button.clicked.connect(self.revert_text)
        self.cancel_button = QPushButton(strings.ED_CANCEL)
        self.cancel_button.setObjectName("SecondaryButton")
        self.cancel_button.clicked.connect(self.cancel)
        self.save_button = QPushButton(strings.ED_SAVE)
        self.save_button.setObjectName("PrimaryButton")
        self.save_button.clicked.connect(self.save)
        row.addWidget(self.revert_button)
        row.addStretch(1)
        row.addWidget(self.cancel_button)
        row.addWidget(self.save_button)
        column.addLayout(row)
        return footer

    def _build_shortcuts(self) -> None:
        context = Qt.ShortcutContext.WidgetWithChildrenShortcut
        bindings: list[tuple[str, object]] = [("Ctrl+S", self.save), ("Esc", self.cancel)]
        bindings += [(f"Ctrl+{n}", lambda i=n - 1: self.select_correct(i)) for n in range(1, 6)]
        self._shortcuts: list[QShortcut] = []
        for keys, handler in bindings:
            shortcut = QShortcut(key(keys), self)
            shortcut.setContext(context)
            shortcut.activated.connect(handler)  # type: ignore[arg-type]
            self._shortcuts.append(shortcut)

    def _set_tab_order(self) -> None:
        chain = [self.stem_edit, *(r.edit for r in self.option_rows), self.explanation_edit, self.note_edit]
        for first, second in zip(chain, chain[1:], strict=False):
            QWidget.setTabOrder(first, second)
        QWidget.setTabOrder(self.note_edit, self.save_button)

    # ------------------------------------------------------------------ public state
    @property
    def active(self) -> bool:
        return self._session is not None

    @property
    def session(self) -> EditSession | None:
        return self._session

    @property
    def autosaver(self) -> DraftAutosaver:
        return self._autosave

    @property
    def state(self) -> EditorState:
        return self._derive_state()

    @property
    def has_unsaved_draft(self) -> bool:
        return self._draft_exists

    def content(self) -> QuestionContent:
        """The text currently in the fields, exactly as typed."""
        return QuestionContent(
            stem=self.stem_edit.toPlainText(),
            options=tuple(r.edit.toPlainText() for r in self.option_rows),
            correct=AnswerLetter(ANSWER_LETTERS[max(0, self._correct_group.checkedId())]),
            explanation=self.explanation_edit.toPlainText(),
        )

    def note(self) -> str:
        return self.note_edit.text()

    def differs_from_saved(self) -> bool:
        session = self._session
        return session is not None and not same_content(self.content(), session.base_content)

    # ------------------------------------------------------------------ starting
    def begin(self, session: EditSession) -> None:
        """Open the editor on *session* (the caller has ended any earlier session)."""
        self._session = session
        self._seq = 0
        self._conflict = None
        self._flash = None
        self._last_save_error = None
        self._draft_exists = session.restored_draft is not None
        self._draft_saved_at = session.restored_draft.saved_at if session.restored_draft else None
        self._autosave.begin_session()
        self._fill(session.initial_content, session.initial_note)
        self.header_label.setText(f"{session.uid}")
        self.warning_label.setVisible(session.status is ReviewStatus.APPROVED)
        self._show_info(session)
        self.conflict_panel.hide()
        self.validation_label.hide()
        self._clear_invalid()
        self._update_state()
        self.scroll.verticalScrollBar().setValue(0)
        self.stem_edit.setFocus(Qt.FocusReason.OtherFocusReason)

    def _show_info(self, session: EditSession) -> None:
        notes: list[str] = []
        draft = session.restored_draft
        if draft is not None:
            notes.append(
                strings.ED_DRAFT_RESTORED.format(
                    reviewer=draft.reviewer_name, time=format_clock(draft.saved_at)
                )
            )
            if session.base_revision_no != session.current_revision_no:
                notes.append(
                    strings.ED_DRAFT_OUTDATED.format(
                        base=session.base_revision_no, current=session.current_revision_no
                    )
                )
        self.info_label.setText("\n".join(notes))
        self.info_label.setVisible(bool(notes))

    def _fill(self, content: QuestionContent, note: str) -> None:
        """Put text into the fields without treating it as typing."""
        self._loading = True
        try:
            self.stem_edit.set_plain_text_silently(content.stem)
            for row, text in zip(self.option_rows, content.options, strict=True):
                row.edit.set_plain_text_silently(text)
            self.explanation_edit.set_plain_text_silently(content.explanation)
            self.note_edit.blockSignals(True)
            self.note_edit.setText(note)
            self.note_edit.blockSignals(False)
            self._correct_group.button(content.correct.index).setChecked(True)
            self._mark_correct(content.correct.index)
        finally:
            self._loading = False

    def _mark_correct(self, index: int) -> None:
        for i, row in enumerate(self.option_rows):
            row.set_correct(i == index)

    # ------------------------------------------------------------------ typing → draft
    def select_correct(self, index: int) -> None:
        """Make option *index* (0 = ა … 4 = ე) the correct answer."""
        if self.active and 0 <= index < len(self.option_rows):
            self.option_rows[index].radio.setChecked(True)

    def _on_correct_toggled(self, index: int, checked: bool) -> None:
        if checked:
            self._mark_correct(index)
            self._on_changed()

    def _on_changed(self) -> None:
        session = self._session
        if self._loading or session is None:
            return
        self._flash = None
        self._last_save_error = None
        self._clear_invalid()
        self.validation_label.hide()
        self._seq += 1
        content, note = self.content(), self.note()
        # Nothing to protect while the text equals the saved text and no draft has ever been
        # written; as soon as one exists (or is pending), every change is written, even a revert.
        if (
            not self._draft_exists
            and self._autosave.latest_seq == 0
            and same_content(content, session.base_content)
            and not note.strip()
        ):
            self._update_state()
            return
        reviewer = self._profile.name
        if not reviewer:
            self.reviewerRequired.emit()
            self._update_state()
            return
        self._autosave.schedule(
            DraftSnapshot(
                session_id=self._autosave.session_id,
                seq=self._seq,
                question_id=session.question_id,
                base_revision_id=session.base_revision_id,
                base_lock_version=session.base_lock_version,
                content=content,
                reviewer_name=reviewer,
                note=note or None,
            )
        )
        self._update_state()

    def _on_draft_saved(self, write: DraftWrite) -> None:
        if self._session is None:
            return
        self._draft_exists = True
        self._draft_saved_at = write.saved.saved_at
        self._update_state()
        self.draftChanged.emit()

    def _on_write_failed(self, _error: object) -> None:
        self._update_state()

    # ------------------------------------------------------------------ state line
    def _derive_state(self) -> EditorState:
        if self._busy:
            return EditorState.SAVING
        if self._conflict is not None:
            return EditorState.CONFLICT
        if self._last_save_error is not None or (
            self._autosave.last_error is not None and self._autosave.pending
        ):
            return EditorState.ERROR
        if self._flash is not None:
            return self._flash
        if self._autosave.writing:
            return EditorState.SAVING_DRAFT
        if self._autosave.pending:
            return EditorState.UNSAVED
        if self._draft_exists and self.differs_from_saved():
            return EditorState.DRAFT_SAVED
        return EditorState.CLEAN

    def _state_text(self, state: EditorState) -> str:
        session = self._session
        if state is EditorState.CLEAN:
            # right after a save that changed nothing, say so (no revision was created)
            return self._flash_text if self._flash is EditorState.CLEAN else strings.ED_STATE_CLEAN
        if state is EditorState.UNSAVED:
            return strings.ED_STATE_UNSAVED
        if state is EditorState.SAVING_DRAFT:
            return strings.ED_STATE_SAVING_DRAFT
        if state is EditorState.DRAFT_SAVED:
            return strings.ED_STATE_DRAFT_SAVED.format(time=format_clock(self._draft_saved_at))
        if state is EditorState.SAVING:
            return strings.ED_STATE_SAVING
        if state is EditorState.SAVED:
            return self._flash_text or strings.ED_STATE_SAVED.format(
                number=session.base_revision_no if session else 0
            )
        if state is EditorState.CONFLICT:
            return strings.ED_STATE_CONFLICT
        error = self._last_save_error or self._autosave.last_error
        if isinstance(error, StorageFailure):
            detail = storage_text(error) + strings.ED_STORAGE_RETAINED
        else:
            detail = str(error or "")
        return strings.ED_STATE_ERROR.format(detail=detail)

    def _update_state(self) -> None:
        state = self._derive_state()
        text = self._state_text(state)
        changed = self.state_label.property("state") != state.value
        self.state_label.setProperty("state", state.value)
        self.state_label.setText(text)
        if changed:
            repolish(self.state_label)
            self.stateChanged.emit(state)
        busy = state is EditorState.SAVING
        self.save_button.setEnabled(self.active and not busy)
        self.cancel_button.setEnabled(self.active and not busy)
        self.revert_button.setEnabled(self.active and not busy and self.differs_from_saved())

    # ------------------------------------------------------------------ validation display
    def _clear_invalid(self) -> None:
        for edit in (self.stem_edit, self.explanation_edit, *(r.edit for r in self.option_rows)):
            if edit.property("invalid"):
                edit.setProperty("invalid", False)
                repolish(edit)

    def _show_problems(self, problems: list[FieldProblem]) -> None:
        fields = {
            EditField.STEM: self.stem_edit,
            EditField.EXPLANATION: self.explanation_edit,
            **{
                f: r.edit
                for f, r in zip(
                    (
                        EditField.OPTION_A,
                        EditField.OPTION_B,
                        EditField.OPTION_C,
                        EditField.OPTION_D,
                        EditField.OPTION_E,
                    ),
                    self.option_rows,
                    strict=True,
                )
            },
        }
        first: QWidget | None = None
        for problem in problems:
            edit = fields.get(problem.field) if problem.field is not None else None
            if edit is not None:
                edit.setProperty("invalid", True)
                repolish(edit)
                first = first or edit
        lines = [strings.ED_VALIDATION_INTRO, *("• " + problem_text(p) for p in problems)]
        self.validation_label.setText("\n".join(lines))
        self.validation_label.show()
        (first or self.note_edit).setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------ dialogs (overridable in tests)
    def confirm_revoke_approval(self, revision_no: int) -> bool:
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            strings.ED_CONFIRM_REVOKE_TITLE,
            strings.ED_CONFIRM_REVOKE_TEXT.format(number=revision_no),
            parent=self,
        )
        yes = box.addButton(strings.ED_SAVE.split(" (")[0], QMessageBox.ButtonRole.AcceptRole)
        box.addButton(strings.ED_CANCEL.split(" (")[0], QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def ask_cancel_choice(self) -> CancelChoice:
        box = QMessageBox(
            QMessageBox.Icon.Question, strings.ED_CANCEL_TITLE, strings.ED_CANCEL_TEXT, parent=self
        )
        keep = box.addButton(strings.ED_CANCEL_KEEP, QMessageBox.ButtonRole.AcceptRole)
        discard = box.addButton(strings.ED_CANCEL_DISCARD, QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(strings.ED_CANCEL_CONTINUE, QMessageBox.ButtonRole.RejectRole)
        box.exec()
        clicked = box.clickedButton()
        if clicked is keep:
            return CancelChoice.KEEP_DRAFT
        return CancelChoice.DISCARD if clicked is discard else CancelChoice.CONTINUE

    def ask_revert_confirm(self) -> bool:
        answer = QMessageBox.question(
            self,
            strings.ED_REVERT_TITLE,
            strings.ED_REVERT_TEXT,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def ask_continue_without_saving(self, detail: str, *, closing: bool = False) -> bool:
        """The draft could not be written: leave (or close the program) anyway, losing the
        unsaved text? The default answer is "no"."""
        text = strings.ED_CLOSE_FAILED_TEXT if closing else strings.ED_FLUSH_FAILED_TEXT
        answer = QMessageBox.warning(
            self,
            strings.ED_FLUSH_FAILED_TITLE,
            text.format(detail=detail),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    # ------------------------------------------------------------------ explicit save
    def save(self) -> bool:
        """Save the text as a new revision (Ctrl+S). Returns whether the save completed."""
        session = self._session
        if session is None or self._busy:
            return False
        content, note = self.content(), self.note()
        problems = validate_content(content, note)
        if problems:
            self._show_problems(problems)
            self._update_state()
            return False
        self._autosave.flush()  # the text is in the draft table before the save is attempted
        if session.status is ReviewStatus.APPROVED and not self.confirm_revoke_approval(
            session.current_revision_no
        ):
            self._update_state()
            return False
        return self._commit(content, note, rebased_from=None)

    def _commit(self, content: QuestionContent, note: str, rebased_from: int | None) -> bool:
        session = self._session
        if session is None:
            return False
        self._busy = True
        self._update_state()
        QApplication.processEvents()  # let "ინახება…" paint before the (short) blocking transaction
        try:
            result = self._edit_service.save(
                session.question_id,
                session.base_lock_version,
                content,
                note,
                rebased_from_revision_no=rebased_from,
            )
        except EditConflict as exc:
            self._busy = False
            self._on_conflict(exc)
            return False
        except ValidationFailed as exc:
            self._busy = False
            self._show_problems(exc.problems)
            self._update_state()
            return False
        except ReviewerRequired:
            self._busy = False
            self.reviewerRequired.emit()
            self._last_save_error = None
            self.info_label.setText(strings.ED_NEED_REVIEWER)
            self.info_label.show()
            self._update_state()
            return False
        except EditTargetMissing:
            self._busy = False
            self.info_label.setText(strings.ED_TARGET_MISSING)
            self.info_label.show()
            self._update_state()
            return False
        except StorageFailure as exc:
            self._busy = False
            self._last_save_error = exc
            self._update_state()
            return False
        self._busy = False
        self._after_save(result, content)
        return True

    def _after_save(self, result: SaveResult, content: QuestionContent) -> None:
        session = self._session
        if session is None:
            return
        normalized = normalize_content(content)
        self._conflict = None
        self.conflict_panel.hide()
        self._session = replace(
            session,
            base_revision_id=result.revision_id,
            base_revision_no=result.revision_no,
            base_lock_version=result.lock_version,
            base_content=normalized if result.created else session.base_content,
            status=result.status_after,
            current_revision_no=result.revision_no,
            initial_content=normalized,
            restored_draft=None,
        )
        self._autosave.begin_session()  # everything was flushed; the draft row was removed by the commit
        self._seq = 0
        self._draft_exists = self._drafts.load_draft(session.question_id) is not None
        self._fill(normalized, "" if result.created else self.note())
        self.warning_label.setVisible(result.status_after is ReviewStatus.APPROVED)
        self.info_label.hide()
        self._last_save_error = None
        self._flash = EditorState.SAVED if result.created else EditorState.CLEAN
        self._flash_text = (
            strings.ED_STATE_SAVED.format(number=result.revision_no)
            if result.created
            else strings.ED_STATE_UNCHANGED
        )
        self._update_state()
        self.saved.emit(result)
        self.draftChanged.emit()

    # ------------------------------------------------------------------ conflicts
    def _on_conflict(self, exc: EditConflict) -> None:
        self._conflict = exc
        self.conflict_label.setText(strings.ED_CONFLICT_TEXT.format(current=exc.current_revision_no))
        self.conflict_panel.show()
        self.scroll.verticalScrollBar().setValue(0)
        self._update_state()

    def _save_over_newer(self) -> bool:
        """The reviewer knowingly saves their text on top of the newer revision (which stays
        in the history). Asks again if that revision is approved."""
        session, conflict = self._session, self._conflict
        if session is None or conflict is None:
            return False
        content, note = self.content(), self.note()
        if conflict.current_status is ReviewStatus.APPROVED and not self.confirm_revoke_approval(
            conflict.current_revision_no
        ):
            return False
        self._session = replace(
            session,
            base_lock_version=conflict.current_lock_version,
            current_revision_no=conflict.current_revision_no,
            status=conflict.current_status,
        )
        self._conflict = None
        self.conflict_panel.hide()
        return self._commit(content, note, rebased_from=session.base_revision_no)

    def _request_compare(self) -> None:
        if self._session is not None:
            self.compareRequested.emit(self._session, self.content())

    def _keep_draft_and_leave(self) -> None:
        """Leave edit mode; the text stays as a draft (marked outdated) next to the newer revision."""
        if self.leave():
            self.finished.emit()

    # ------------------------------------------------------------------ leaving
    def flush_draft(self) -> bool:
        """Write any pending draft now (filter changes, page changes). Stays in edit mode."""
        if not self.active:
            return True
        ok = self._autosave.flush()
        self._update_state()
        return ok

    def leave(self, *, closing: bool = False) -> bool:
        """End the editing session (navigating away, closing). The draft is flushed first.

        Returns ``False`` — and stays in edit mode with the text intact — if the draft could not
        be written and the reviewer chose not to lose it.
        """
        session = self._session
        if session is None:
            return True
        if not self._autosave.flush():
            error = self._autosave.last_error
            detail = storage_text(error) if isinstance(error, StorageFailure) else str(error or "")
            if not self.ask_continue_without_saving(detail, closing=closing):
                self._update_state()
                return False
        elif not self.differs_from_saved():
            # nothing to keep: a draft that only repeats the saved text is removed
            try:
                if self._drafts.drop_if_identical(session.question_id, session.base_content):
                    self._draft_exists = False
                    self.draftChanged.emit()
            except StorageFailure:
                log.warning("Could not remove an identical draft of question %s", session.uid)
        self._end_session()
        return True

    def _end_session(self) -> None:
        self._autosave.end_session()
        self._session = None
        self._conflict = None
        self._flash = None
        self._update_state()

    def cancel(self) -> bool:
        """The Cancel button / Esc: leave edit mode, asking what to do with unsaved changes."""
        session = self._session
        if session is None or self._busy:
            return True
        if not self.differs_from_saved() and not self._draft_exists:
            self._end_session()
            self.finished.emit()
            return True
        if not self.differs_from_saved() or not self._autosave.flush():
            if not self.leave():
                return False
            self.finished.emit()
            return True
        choice = self.ask_cancel_choice()
        if choice is CancelChoice.CONTINUE:
            return False
        if choice is CancelChoice.DISCARD:
            try:
                self._drafts.discard_draft(
                    session.question_id, self._profile.name or "—", DiscardReason.REVERTED
                )
            except (StorageFailure, ReviewerRequired) as exc:
                self._last_save_error = exc if isinstance(exc, StorageFailure) else None
                self._update_state()
                return False
            self._draft_exists = False
            self.draftChanged.emit()
            self._end_session()
        else:  # keep the draft
            if not self.leave():
                return False
        self.finished.emit()
        return True

    def revert_text(self) -> None:
        """Put the saved text back into the fields (keeps editing)."""
        session = self._session
        if session is None or not self.differs_from_saved():
            return
        if not self.ask_revert_confirm():
            return
        self._fill(session.base_content, "")
        self._on_changed()

    # ------------------------------------------------------------------ shutdown
    def shutdown(self) -> bool:
        """Final flush for application exit; returns whether closing is safe."""
        if not self.active:
            self._autosave.close()
            return True
        return self.leave(closing=True)
