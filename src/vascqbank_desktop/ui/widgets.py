"""Small reusable widgets and helpers shared by the pages."""

from __future__ import annotations

import html
import re

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QSizePolicy, QVBoxLayout, QWidget

from vascqbank_desktop.domain.review_status import ReviewStatus

_BLANK_LINE = re.compile(r"\n[ \t]*\n")


def repolish(widget: QWidget) -> None:
    """Re-apply the style sheet after a dynamic property or object name changed."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def make_label(text: str, object_name: str, *, wrap: bool = True, selectable: bool = False) -> QLabel:
    label = QLabel(text)
    label.setObjectName(object_name)
    label.setWordWrap(wrap)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


def make_card(object_name: str = "Card") -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName(object_name)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(20, 16, 20, 16)
    layout.setSpacing(8)
    return frame, layout


def paragraphs_html(text: str, line_height: int = 155) -> str:
    """Escape *text* completely and lay it out as comfortable paragraphs.

    Blank lines separate paragraphs, single line breaks and runs of spaces are
    kept (``white-space: pre-wrap``), and nothing in *text* can be interpreted
    as markup — clinical text such as ``PaO2 < 60 mmHg & <b>`` shows literally.
    """
    blocks = _BLANK_LINE.split(text.strip("\n"))
    style = f"margin:0 0 9px 0; line-height:{line_height}%; white-space:pre-wrap;"
    return "".join(f'<p style="{style}">{html.escape(block, quote=False)}</p>' for block in blocks)


class SelectableText(QLabel):
    """Wrapping, read-only text that can be selected with the mouse or keyboard and copied."""

    def __init__(self, object_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setWordWrap(True)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self._plain = ""

    def set_content(self, text: str) -> None:
        self._plain = text
        self.setText(paragraphs_html(text))

    @property
    def plain_text(self) -> str:
        """The text exactly as stored, before any layout."""
        return self._plain


class StatusChip(QLabel):
    """Review status as a coloured pill; the label text always names the status."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("StatusChip")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        self._status: ReviewStatus | None = None

    @property
    def status(self) -> ReviewStatus | None:
        return self._status

    def set_status(self, status: ReviewStatus) -> None:
        self._status = status
        self.setText(status.label_ka)
        self.setProperty("status", status.value)
        repolish(self)
