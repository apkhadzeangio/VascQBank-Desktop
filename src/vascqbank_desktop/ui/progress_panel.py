"""Review-progress widgets driven by real database counts (never hard-coded numbers).

* :class:`ProgressPanel` — the Home dashboard: whole bank and the selected
  subject, five figures each plus a proportion bar.
* :class:`ProgressStrip` — a compact two-line version for the catalog page.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import Subject
from vascqbank_desktop.services.question_stats import ProgressSummary
from vascqbank_desktop.ui import strings, theme
from vascqbank_desktop.ui.formatting import format_count, format_percent
from vascqbank_desktop.ui.widgets import make_label

SHORT_LABELS: dict[ReviewStatus, str] = {
    ReviewStatus.PENDING_REVIEW: strings.PROG_SHORT_PENDING,
    ReviewStatus.IN_REVIEW: strings.PROG_SHORT_IN_REVIEW,
    ReviewStatus.APPROVED: strings.PROG_SHORT_APPROVED,
    ReviewStatus.REJECTED: strings.PROG_SHORT_REJECTED,
}
# Left to right: the work that is finished first, what is open last.
_BAR_ORDER = (
    ReviewStatus.APPROVED,
    ReviewStatus.IN_REVIEW,
    ReviewStatus.REJECTED,
    ReviewStatus.PENDING_REVIEW,
)


class StatusBar(QWidget):
    """A thin bar whose coloured segments are proportional to the status counts."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._summary = ProgressSummary(0, {})

    def set_summary(self, summary: ProgressSummary) -> None:
        self._summary = summary
        self.update()

    def segments(self) -> list[tuple[ReviewStatus, float]]:
        """(status, fraction of the bar) for every non-empty status, in drawing order."""
        total = self._summary.total
        if not total:
            return []
        return [(s, self._summary.count(s) / total) for s in _BAR_ORDER if self._summary.count(s)]

    def paintEvent(self, _event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect())
        clip = QPainterPath()
        clip.addRoundedRect(bounds, 4, 4)
        painter.setClipPath(clip)
        painter.fillRect(bounds, QColor("#E7EAF0"))
        x = bounds.left()
        for status, fraction in self.segments():
            width = bounds.width() * fraction
            segment = QRectF(x, bounds.top(), width, bounds.height())
            painter.fillRect(segment, QColor(theme.STATUS_BAR_COLORS[status]))
            x += width


class _Scope(QWidget):
    """One scope: title, five figures with captions, a bar and the approved share."""

    def __init__(self, title: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 4, 0, 4)
        grid.setHorizontalSpacing(24)
        grid.setVerticalSpacing(2)
        self.title_label = make_label(title, "ScopeTitle")
        grid.addWidget(self.title_label, 0, 0, 1, 6)

        self.total_label = make_label("0", "StatNumber", wrap=False)
        grid.addWidget(self.total_label, 1, 0)
        grid.addWidget(make_label(strings.PROG_TOTAL, "StatCaption", wrap=False), 2, 0)
        self.status_labels: dict[ReviewStatus, QLabel] = {}
        for column, status in enumerate(ReviewStatus, start=1):
            number = make_label("0", "StatNumber", wrap=False)
            self.status_labels[status] = number
            grid.addWidget(number, 1, column)
            grid.addWidget(make_label(status.label_ka, "StatCaption"), 2, column, Qt.AlignmentFlag.AlignTop)
        drafts_column = len(ReviewStatus) + 1
        self.drafts_label = make_label("0", "StatNumber", wrap=False)
        grid.addWidget(self.drafts_label, 1, drafts_column)
        grid.addWidget(
            make_label(strings.PROG_DRAFTS, "StatCaption"), 2, drafts_column, Qt.AlignmentFlag.AlignTop
        )
        for column in range(drafts_column + 1):
            grid.setColumnStretch(column, 1)

        self.bar = StatusBar()
        grid.addWidget(self.bar, 3, 0, 1, drafts_column + 1)
        self.share_label = make_label("", "ScopeNote", wrap=False)
        grid.addWidget(self.share_label, 4, 0, 1, drafts_column + 1)

    def set_title(self, title: str) -> None:
        self.title_label.setText(title)

    def set_summary(self, summary: ProgressSummary) -> None:
        self.total_label.setText(format_count(summary.total))
        for status, label in self.status_labels.items():
            label.setText(format_count(summary.count(status)))
        self.drafts_label.setText(format_count(summary.drafts))
        self.bar.set_summary(summary)
        self.share_label.setText(strings.PROG_SHARE.format(percent=format_percent(summary.approved_share)))


class ProgressPanel(QFrame):
    """Home dashboard: counts for the whole bank and for the selected subject.

    ``framed=False`` drops the card frame and title so it can sit inside another card.
    """

    def __init__(self, parent: QWidget | None = None, *, framed: bool = True) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        if framed:
            self.setObjectName("Card")
            outer.setContentsMargins(22, 18, 22, 18)
            outer.addWidget(make_label(strings.PROG_TITLE, "CardTitle"))
        else:
            outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(10)
        self.bank_scope = _Scope(strings.PROG_BANK)
        self.subject_scope = _Scope("")
        self.subject_hint = make_label(strings.PROG_SUBJECT_NONE, "ScopeNote")
        outer.addWidget(self.bank_scope)
        outer.addWidget(self.subject_scope)
        outer.addWidget(self.subject_hint)
        self.set_progress(ProgressSummary(0, {}), None, None)

    def set_progress(
        self, bank: ProgressSummary, subject: ProgressSummary | None, subject_name: str | None
    ) -> None:
        self.bank_scope.set_summary(bank)
        has_subject = subject is not None and subject_name is not None
        self.subject_scope.setVisible(has_subject)
        self.subject_hint.setVisible(not has_subject)
        if subject is not None and subject_name is not None:
            self.subject_scope.set_title(subject_name)
            self.subject_scope.set_summary(subject)


class _StripRow:
    """The labels of one strip line; they live in the strip's shared grid, so every
    line has its columns at the same x position whatever the numbers' widths."""

    def __init__(self, grid: QGridLayout, row: int) -> None:
        self.scope_label = make_label("", "StripScope", wrap=False)
        self.total_label = make_label("", "StripCount", wrap=False)
        grid.addWidget(self.scope_label, row, 0)
        grid.addWidget(self.total_label, row, 1)
        self.status_labels: dict[ReviewStatus, QLabel] = {}
        for column, status in enumerate(ReviewStatus, start=2):
            label = QLabel()
            label.setObjectName("StripCount")
            label.setTextFormat(Qt.TextFormat.RichText)
            self.status_labels[status] = label
            grid.addWidget(label, row, column)
        self.drafts_label = QLabel()
        self.drafts_label.setObjectName("StripCount")
        self.drafts_label.setTextFormat(Qt.TextFormat.RichText)
        grid.addWidget(self.drafts_label, row, len(ReviewStatus) + 2)
        self.widgets = [self.scope_label, self.total_label, *self.status_labels.values(), self.drafts_label]

    def set_visible(self, visible: bool) -> None:
        for widget in self.widgets:
            widget.setVisible(visible)

    def set_summary(self, scope: str, tooltip: str, summary: ProgressSummary) -> None:
        self.scope_label.setText(scope)
        self.scope_label.setToolTip(tooltip)
        self.total_label.setText(strings.PROG_STRIP_TOTAL.format(count=format_count(summary.total)))
        for status, label in self.status_labels.items():
            dot = f'<span style="color:{theme.STATUS_BAR_COLORS[status]}">●</span>'
            label.setText(f"{dot} {SHORT_LABELS[status]}: <b>{format_count(summary.count(status))}</b>")
        self.drafts_label.setText(f"✎ {strings.PROG_SHORT_DRAFTS}: <b>{format_count(summary.drafts)}</b>")


class ProgressStrip(QFrame):
    """Compact catalog header: a line for the whole bank and a line for the open subject."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("ProgressStrip")
        grid = QGridLayout(self)
        grid.setContentsMargins(14, 8, 14, 8)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(2)
        grid.setColumnMinimumWidth(0, 100)
        grid.setColumnMinimumWidth(1, 92)
        for column in range(2, len(ReviewStatus) + 3):
            grid.setColumnStretch(column, 1)
        self.bank_line = _StripRow(grid, 0)
        self.subject_line = _StripRow(grid, 1)
        self.set_progress(ProgressSummary(0, {}), None, None)

    def set_progress(
        self, bank: ProgressSummary, subject: ProgressSummary | None, subject_info: Subject | None
    ) -> None:
        self.bank_line.set_summary(strings.PROG_STRIP_BANK, strings.PROG_BANK, bank)
        has_subject = subject is not None and subject_info is not None
        self.subject_line.set_visible(has_subject)
        if subject is not None and subject_info is not None:
            self.subject_line.set_summary(subject_info.code, subject_info.name_ka, subject)
