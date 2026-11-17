"""Read-only question detail panel.

Shows the *current* revision exactly as stored: immutable ID, Georgian subject,
stem, the five options ა–ე with the correct one highlighted, the explanation,
status, revision number and source facts. Text wraps naturally and can be
selected and copied; there are deliberately no edit or approval controls —
those arrive with the editor in a later phase.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.answers import ANSWER_LETTERS
from vascqbank_desktop.domain.catalog import QuestionDetail
from vascqbank_desktop.domain.question import SourceKind
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.formatting import format_timestamp, question_plain_text
from vascqbank_desktop.ui.widgets import SelectableText, StatusChip, make_label, repolish

COPIED_FEEDBACK_MS = 1600


class OptionRow(QFrame):
    """One answer option: letter badge, text and — for the correct one — an explicit tag."""

    def __init__(self, letter: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("OptionCard")
        self.letter = letter
        self.setProperty("correct", False)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 10, 14, 10)
        row.setSpacing(12)
        self.badge = QLabel(letter)
        self.badge.setObjectName("OptionLetter")
        self.badge.setFixedSize(30, 30)
        self.badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.badge, 0, Qt.AlignmentFlag.AlignTop)
        self.text_label = SelectableText("OptionText")
        row.addWidget(self.text_label, 1)
        self.correct_tag = QLabel(strings.DET_CORRECT_TAG)
        self.correct_tag.setObjectName("CorrectTag")
        self.correct_tag.setVisible(False)
        row.addWidget(self.correct_tag, 0, Qt.AlignmentFlag.AlignTop)

    @property
    def is_correct(self) -> bool:
        return bool(self.property("correct"))

    def set_option(self, text: str, *, correct: bool) -> None:
        self.text_label.set_content(text)
        self.setProperty("correct", correct)
        self.correct_tag.setVisible(correct)
        repolish(self)
        repolish(self.badge)


class QuestionDetailView(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._detail: QuestionDetail | None = None

        self._stack = QStackedWidget()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._stack)

        self.placeholder = make_label(strings.CAT_SELECT_PROMPT, "EmptyListMessage")
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._stack.addWidget(self.placeholder)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("DetailScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        body.setObjectName("DetailBody")
        self.scroll.setWidget(body)
        self._stack.addWidget(self.scroll)

        page = QVBoxLayout(body)
        page.setContentsMargins(4, 2, 12, 16)
        page.setSpacing(14)
        page.addWidget(self._build_header())
        page.addWidget(self._build_stem_card())
        page.addWidget(self._build_options_card())
        page.addWidget(self._build_explanation_card())
        page.addWidget(self._build_source_card())
        self.read_only_note = make_label(strings.DET_READ_ONLY, "ReadOnlyNote")
        page.addWidget(self.read_only_note)
        page.addStretch(1)

    # ------------------------------------------------------------------ construction
    def _build_header(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 14, 20, 14)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.uid_label = make_label("", "DetailUid", wrap=False, selectable=True)
        self.status_chip = StatusChip()
        self.revision_badge = make_label("", "RevisionBadge", wrap=False)
        self.revision_badge.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        top.addWidget(self.uid_label)
        top.addWidget(self.status_chip, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addWidget(self.revision_badge, 0, Qt.AlignmentFlag.AlignVCenter)
        top.addStretch(1)
        layout.addLayout(top)

        # The subject name can be long and wraps; the copy button sits beside it, so
        # the header never needs more width than the panel has.
        bottom = QHBoxLayout()
        bottom.setSpacing(12)
        self.subject_label = make_label("", "DetailSubject", selectable=True)
        bottom.addWidget(self.subject_label, 1)
        self.copy_button = QPushButton(strings.DET_COPY)
        self.copy_button.setObjectName("SecondaryButton")
        self.copy_button.clicked.connect(self.copy_to_clipboard)
        self._copied_timer = QTimer(self)  # owned by this widget, so it can never outlive it
        self._copied_timer.setSingleShot(True)
        self._copied_timer.setInterval(COPIED_FEEDBACK_MS)
        self._copied_timer.timeout.connect(lambda: self.copy_button.setText(strings.DET_COPY))
        bottom.addWidget(self.copy_button, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(bottom)
        return frame

    def _section(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        frame = QFrame()
        frame.setObjectName("Card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)
        layout.addWidget(make_label(title, "SectionTitle", wrap=False))
        return frame, layout

    def _build_stem_card(self) -> QWidget:
        frame, layout = self._section(strings.DET_QUESTION)
        self.stem_label = SelectableText("StemText")
        layout.addWidget(self.stem_label)
        return frame

    def _build_options_card(self) -> QWidget:
        frame, layout = self._section(strings.DET_OPTIONS)
        self.option_rows = [OptionRow(letter) for letter in ANSWER_LETTERS]
        for row in self.option_rows:
            layout.addWidget(row)
        return frame

    def _build_explanation_card(self) -> QWidget:
        frame, layout = self._section(strings.DET_EXPLANATION)
        self.explanation_label = SelectableText("ExplanationText")
        layout.addWidget(self.explanation_label)
        return frame

    def _build_source_card(self) -> QWidget:
        frame, layout = self._section(strings.DET_SOURCE)
        grid = QGridLayout()
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(6)
        grid.setColumnStretch(1, 1)
        self.metadata: dict[str, QLabel] = {}
        self._metadata_names: dict[str, QLabel] = {}
        rows = [
            ("revision", strings.DET_REVISION_FIELD),
            ("source_kind", strings.DET_SOURCE_KIND),
            ("source_file", strings.DET_SOURCE_FILE),
            ("import_batch", strings.DET_IMPORT_BATCH),
            ("imported_at", strings.DET_IMPORTED_AT),
            ("modified_at", strings.DET_MODIFIED_AT),
            ("approved_at", strings.DET_APPROVED_AT),
        ]
        for index, (key, caption) in enumerate(rows):
            name = make_label(caption, "FieldName", wrap=False)
            value = make_label("", "FieldValue", selectable=True)
            grid.addWidget(name, index, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, index, 1)
            self.metadata[key] = value
            self._metadata_names[key] = name
        layout.addLayout(grid)
        return frame

    # ------------------------------------------------------------------ content
    @property
    def detail(self) -> QuestionDetail | None:
        return self._detail

    @property
    def question_uid(self) -> str | None:
        return self._detail.uid if self._detail else None

    def correct_letter(self) -> str | None:
        """The letter whose option is currently drawn as the correct one."""
        marked = [row.letter for row in self.option_rows if row.is_correct]
        return marked[0] if len(marked) == 1 else None

    def clear(self) -> None:
        self._detail = None
        self._stack.setCurrentWidget(self.placeholder)

    def show_question(self, detail: QuestionDetail) -> None:
        self._detail = detail
        content = detail.content
        self.uid_label.setText(detail.uid)
        self.subject_label.setText(detail.subject_name_ka)
        self.status_chip.set_status(detail.status)
        self.revision_badge.setText(strings.DET_REVISION.format(number=detail.revision_no))
        self.stem_label.set_content(content.stem)
        for row, text in zip(self.option_rows, content.options, strict=True):
            row.set_option(text, correct=row.letter == content.correct.value)
        self.explanation_label.set_content(content.explanation)

        paired = detail.source_kind is SourceKind.PAIRED_TXT
        kind = strings.DET_KIND_PAIRED if paired else strings.DET_KIND_MASTER
        values = {
            "revision": f"{detail.revision_no}",
            "source_kind": kind,
            "source_file": f"{detail.source_file} : {detail.source_line}",
            "import_batch": f"№ {detail.import_batch_id}",
            "imported_at": format_timestamp(detail.created_at),
            "modified_at": format_timestamp(detail.modified_at),
            "approved_at": format_timestamp(detail.approved_at),
        }
        for key, text in values.items():
            self.metadata[key].setText(text)
        approved = detail.approved_at is not None
        self.metadata["approved_at"].setVisible(approved)
        self._metadata_names["approved_at"].setVisible(approved)

        self._stack.setCurrentWidget(self.scroll)
        self.scroll.verticalScrollBar().setValue(0)  # a new question starts at its top

    # ------------------------------------------------------------------ clipboard
    def copy_to_clipboard(self) -> None:
        if self._detail is None:
            return
        QGuiApplication.clipboard().setText(question_plain_text(self._detail))
        self.copy_button.setText(strings.DET_COPIED)
        self._copied_timer.start()
