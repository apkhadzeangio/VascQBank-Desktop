"""Plain-text multi-line editing widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QPlainTextEdit, QSizePolicy, QWidget


class AutoGrowTextEdit(QPlainTextEdit):
    """A plain-text editor that grows with its text, so the page scrolls instead of the field.

    * **Plain text only** — a :class:`QPlainTextEdit` cannot hold formatting, and pasting rich
      text inserts just its text: no unwanted bold, colours or fonts ever reach the database.
    * **Tab / Shift+Tab move between fields** (keyboard navigation); a tab character is not
      typed. Enter starts a new line.
    * The height follows the number of wrapped lines between *min_lines* and *max_lines*;
      beyond that the field scrolls internally.
    """

    def __init__(
        self,
        object_name: str = "EditorField",
        *,
        min_lines: int = 2,
        max_lines: int = 40,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName(object_name)
        self._min_lines = min_lines
        self._max_lines = max_lines
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(self._fit)
        self._fit()

    def _fit(self, *_args: object) -> None:
        lines = max(self._min_lines, min(self._max_lines, int(self.document().size().height())))
        spacing = self.fontMetrics().lineSpacing()
        # frame + document margins + the style sheet's vertical padding (6 px twice) and a hair
        extra = 2 * self.frameWidth() + int(2 * self.document().documentMargin()) + 14
        wanted = lines * spacing + extra
        if self.height() != wanted or self.minimumHeight() != wanted:
            self.setFixedHeight(wanted)
        overflow = int(self.document().size().height()) > self._max_lines
        self.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded if overflow else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

    def resizeEvent(self, event) -> None:  # noqa: ANN001, N802
        super().resizeEvent(event)
        self._fit()  # a new width changes how many lines the text wraps to

    def set_plain_text_silently(self, text: str) -> None:
        """Replace the text without emitting ``textChanged`` (used when loading a question)."""
        blocked = self.blockSignals(True)
        try:
            self.setPlainText(text)
        finally:
            self.blockSignals(blocked)
        self._fit()
