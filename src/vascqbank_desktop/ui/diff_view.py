"""Word-level comparison of two versions of a question (read-only)."""

from __future__ import annotations

import html
from collections.abc import Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.editing import EditField
from vascqbank_desktop.domain.textdiff import DiffKind, DiffSegment, FieldDiff
from vascqbank_desktop.ui import strings, theme
from vascqbank_desktop.ui.widgets import SelectableText, make_label


def segments_html(segments: Sequence[DiffSegment]) -> str:
    """The diff as escaped HTML: removed text struck through on red, added text on green."""
    parts: list[str] = []
    for segment in segments:
        text = html.escape(segment.text, quote=False)
        if segment.kind is DiffKind.REMOVED:
            style = (
                f"background:{theme.DIFF_REMOVED_BG};color:{theme.DIFF_REMOVED_FG};"
                "text-decoration:line-through;"
            )
        elif segment.kind is DiffKind.ADDED:
            style = f"background:{theme.DIFF_ADDED_BG};color:{theme.DIFF_ADDED_FG};"
        else:
            parts.append(text)
            continue
        parts.append(f'<span style="{style}">{text}</span>')
    return f'<p style="margin:0; line-height:150%; white-space:pre-wrap;">{"".join(parts)}</p>'


def field_title(field: EditField) -> str:
    if field is EditField.STEM:
        return strings.ED_FIELD_STEM
    if field is EditField.EXPLANATION:
        return strings.ED_FIELD_EXPLANATION
    if field is EditField.CORRECT:
        return strings.DIFF_FIELD_CORRECT
    return strings.ED_FIELD_OPTION.format(letter=field.option_letter)


class DiffView(QWidget):
    """Shows what differs between an old and a new text, field by field."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setObjectName("DetailScroll")
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        outer.addWidget(self.scroll)
        self._body = QWidget()
        self._body.setObjectName("DetailBody")
        self.scroll.setWidget(self._body)
        self._column = QVBoxLayout(self._body)
        self._column.setContentsMargins(4, 2, 12, 12)
        self._column.setSpacing(10)
        self.title_label = make_label("", "CardTitle")
        self.legend_label = make_label(strings.DIFF_LEGEND, "DiffLegend")
        self.field_labels: dict[EditField, SelectableText] = {}
        self.changed_fields: tuple[EditField, ...] = ()
        self.message_label = make_label("", "CardBody")
        self._cards: list[QWidget] = []

    def set_comparison(self, diffs: Sequence[FieldDiff], *, title: str = "") -> None:
        for widget in self._cards:
            self._column.removeWidget(widget)
            widget.deleteLater()
        self._cards.clear()
        self.field_labels.clear()

        def add(widget: QWidget) -> None:
            self._column.insertWidget(self._column.count(), widget)
            self._cards.append(widget)

        self.title_label.setText(title)
        self.title_label.setVisible(bool(title))
        for widget in (self.title_label, self.legend_label):
            self._column.removeWidget(widget)
        self._column.insertWidget(0, self.title_label)
        self._column.insertWidget(1, self.legend_label)

        self.changed_fields = tuple(d.field for d in diffs if d.changed)
        for diff in diffs:
            if not diff.changed:
                continue
            card = QFrame()
            card.setObjectName("Card")
            layout = QVBoxLayout(card)
            layout.setContentsMargins(16, 12, 16, 14)
            layout.setSpacing(6)
            layout.addWidget(make_label(field_title(diff.field), "SectionTitle", wrap=False))
            label = SelectableText("ExplanationText")
            label.setText(segments_html(diff.segments))
            layout.addWidget(label)
            self.field_labels[diff.field] = label
            add(card)
        unchanged = [field_title(d.field) for d in diffs if not d.changed]
        if not self.changed_fields:
            self.message_label.setText(strings.HI_NO_DIFFERENCES)
        elif unchanged:
            self.message_label.setText(strings.DIFF_UNCHANGED_FIELDS.format(fields=", ".join(unchanged)))
        else:
            self.message_label.setText("")
        self.message_label.setVisible(bool(self.message_label.text()))
        add(self.message_label)
        self._column.addStretch(1)


class CompareDialog(QDialog):
    """A window showing a comparison (e.g. a conflicting text against the newer revision)."""

    def __init__(self, title: str, diffs: Sequence[FieldDiff], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(strings.DIFF_COMPARE_TITLE)
        self.resize(860, 640)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        self.view = DiffView()
        self.view.set_comparison(diffs, title=title)
        layout.addWidget(self.view, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        close = QPushButton(strings.DR_CLOSE)
        close.setObjectName("PrimaryButton")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        layout.addLayout(row)
