"""Keyboard shortcuts written as text, parsed independently of the interface language.

``QKeySequence("Ctrl+Enter")`` parses its text in Qt's *native* format, which follows the installed
translation: with Qt's Georgian catalogue both "Enter" and "Return" are read as the Return key, so
the two approve shortcuts (Ctrl+Return and the keypad's Ctrl+Enter) became the *same* sequence —
an ambiguous pair Qt refuses to trigger at all. Every shortcut in the program is therefore built
with :func:`key`, which always parses the portable (English) notation.
"""

from __future__ import annotations

from PySide6.QtGui import QKeySequence


def key(text: str) -> QKeySequence:
    """The shortcut named by *text* in Qt's portable notation (e.g. ``"Ctrl+Shift+Enter"``)."""
    sequence = QKeySequence.fromString(text, QKeySequence.SequenceFormat.PortableText)
    if sequence.isEmpty():
        raise ValueError(f"not a key sequence: {text!r}")
    return sequence
