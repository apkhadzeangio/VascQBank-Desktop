"""Paints one catalog row: ID, status chip, where the search matched, and a short preview."""

from __future__ import annotations

from PySide6.QtCore import QModelIndex, QPersistentModelIndex, QPointF, QRect, QRectF, QSize, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QPainter,
    QPen,
    QTextCharFormat,
    QTextLayout,
    QTextOption,
)
from PySide6.QtWidgets import QStyle, QStyledItemDelegate, QStyleOptionViewItem

from vascqbank_desktop.domain.search import Preview, PreviewField
from vascqbank_desktop.ui import strings, theme
from vascqbank_desktop.ui.question_list_model import SummaryRole

ROW_HEIGHT = 78
PADDING_X = 16
PREVIEW_LINES = 2


def fit_preview(
    text: str,
    spans: tuple[tuple[int, int], ...],
    font: QFont,
    width: int,
    max_lines: int = PREVIEW_LINES,
) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Fit *text* into *max_lines* wrapped lines of *width* pixels.

    Text that does not fit is cut at the start of the last visible line and that
    line is elided with ``…``. Highlight spans are clipped to what remains.
    """
    layout = _layout(text, font, width, max_lines + 1)
    if len(layout) > max_lines:
        last_start = layout[max_lines - 1]
        elided = QFontMetrics(font).elidedText(text[last_start:], Qt.TextElideMode.ElideRight, width)
        text = text[:last_start] + elided
    limit = len(text)
    clipped = tuple((begin, min(end, limit)) for begin, end in spans if begin < limit)
    return text, clipped


def _layout(text: str, font: QFont, width: int, max_lines: int) -> list[int]:
    """Start offsets of the wrapped lines (at most *max_lines*)."""
    layout = QTextLayout(text, font)
    option = QTextOption()
    option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option)
    starts: list[int] = []
    layout.beginLayout()
    while len(starts) < max_lines:
        line = layout.createLine()
        if not line.isValid():
            break
        line.setLineWidth(width)
        starts.append(line.textStart())
    layout.endLayout()
    return starts


def field_tag(preview: Preview) -> str:
    if preview.field is PreviewField.ID:
        return strings.PREVIEW_FIELD_ID
    if preview.field is PreviewField.OPTION:
        return strings.PREVIEW_FIELD_OPTION.format(letter=preview.option_letter or "")
    if preview.field is PreviewField.EXPLANATION:
        return strings.PREVIEW_FIELD_EXPLANATION
    return strings.PREVIEW_FIELD_STEM


class QuestionDelegate(QStyledItemDelegate):
    def __init__(self, parent=None) -> None:  # noqa: ANN001
        super().__init__(parent)
        self.show_field_tags = False  # only meaningful while searching

    def sizeHint(  # noqa: N802
        self, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> QSize:
        return QSize(option.rect.width(), ROW_HEIGHT)

    def paint(  # noqa: N802
        self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex | QPersistentModelIndex
    ) -> None:
        summary = index.data(SummaryRole)
        rect = option.rect
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        background = theme.ACCENT_SOFT if selected else ("#F6F7F9" if hovered else theme.CARD_BG)
        painter.fillRect(rect, QColor(background))
        if selected:
            painter.fillRect(QRect(rect.left(), rect.top(), 4, rect.height()), QColor(theme.ACCENT))
        painter.setPen(QPen(QColor(theme.BORDER)))
        painter.drawLine(rect.bottomLeft(), rect.bottomRight())

        if summary is not None:
            has_draft = summary.id in getattr(index.model(), "draft_ids", ())
            self._paint_summary(painter, option, rect, summary, has_draft)
        painter.restore()

    def _paint_summary(
        self, painter: QPainter, option: QStyleOptionViewItem, rect: QRect, summary, has_draft: bool  # noqa: ANN001
    ) -> None:
        inner = rect.adjusted(PADDING_X, 10, -PADDING_X, -8)

        bold = QFont(option.font)
        bold.setBold(True)
        bold_metrics = QFontMetrics(bold)
        uid_width = bold_metrics.horizontalAdvance(summary.uid)
        painter.setFont(bold)
        painter.setPen(QColor(theme.TEXT))
        painter.drawText(
            QRect(inner.left(), inner.top(), uid_width + 2, 20),
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            summary.uid,
        )

        small = QFont(option.font)
        small.setPointSizeF(max(7.5, option.font.pointSizeF() - 1.5))
        small_metrics = QFontMetrics(small)

        chip_text = summary.status.label_ka
        chip_width = small_metrics.horizontalAdvance(chip_text) + 20
        chip_rect = QRect(inner.right() - chip_width + 1, inner.top(), chip_width, 20)
        foreground, chip_background = theme.STATUS_COLORS[summary.status]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(chip_background))
        painter.drawRoundedRect(chip_rect, 10, 10)
        painter.setFont(small)
        painter.setPen(QColor(foreground))
        painter.drawText(chip_rect, int(Qt.AlignmentFlag.AlignCenter), chip_text)

        marker_width = 0
        if has_draft:
            marker = "✎ " + strings.DR_MARKER
            marker_width = small_metrics.horizontalAdvance(marker) + 10
            painter.setFont(small)
            painter.setPen(QColor(theme.DRAFT_MARKER))
            painter.drawText(
                QRect(inner.left() + uid_width + 10, inner.top(), marker_width, 20),
                int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                marker,
            )

        if self.show_field_tags:
            gap = 10
            tag_left = inner.left() + uid_width + gap + marker_width
            tag_width = chip_rect.left() - tag_left - gap
            if tag_width >= 40:
                tag = small_metrics.elidedText(
                    "· " + field_tag(summary.preview), Qt.TextElideMode.ElideRight, tag_width
                )
                painter.setPen(QColor(theme.TEXT_MUTED))
                painter.drawText(
                    QRect(tag_left, inner.top(), tag_width, 20),
                    int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
                    tag,
                )

        preview_rect = QRect(inner.left(), inner.top() + 26, inner.width(), inner.height() - 26)
        self._paint_preview(painter, option.font, preview_rect, summary.preview)

    def _paint_preview(self, painter: QPainter, font: QFont, rect: QRect, preview: Preview) -> None:
        text, spans = fit_preview(preview.text, preview.spans, font, rect.width())
        layout = QTextLayout(text, font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option)
        y = 0.0
        layout.beginLayout()
        for _ in range(PREVIEW_LINES):
            line = layout.createLine()
            if not line.isValid():
                break
            line.setLineWidth(rect.width())
            line.setPosition(QPointF(0, y))
            y += line.height()
        layout.endLayout()

        highlight = QTextCharFormat()
        highlight.setBackground(QColor(theme.HIGHLIGHT))
        selections = []
        for begin, end in spans:
            fragment = QTextLayout.FormatRange()
            fragment.start = begin
            fragment.length = end - begin
            fragment.format = highlight
            selections.append(fragment)

        painter.setFont(font)
        painter.setPen(QColor("#3A4452"))
        painter.save()
        painter.setClipRect(QRectF(rect))
        layout.draw(painter, QPointF(rect.left(), rect.top()), selections)
        painter.restore()
