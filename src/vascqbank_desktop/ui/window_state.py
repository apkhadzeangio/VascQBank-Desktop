"""Saving and restoring the main window's geometry safely.

A geometry saved on a two-monitor desk can describe a window that is entirely
off-screen on a laptop. :func:`restore_geometry` therefore applies a saved
geometry only if the result is still reachable on a connected screen, and
otherwise reports failure so the caller keeps its default size and position.
"""

from __future__ import annotations

import base64
import binascii
from collections.abc import Iterable

from PySide6.QtCore import QByteArray, QRect
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QWidget

MIN_VISIBLE_WIDTH = 160  # how much of the window must overlap a screen
MIN_VISIBLE_HEIGHT = 80


def is_reachable(frame: QRect, screens: Iterable[QRect]) -> bool:
    """Whether enough of *frame* overlaps one of the *screens* to grab and move the window."""
    for screen in screens:
        overlap = frame.intersected(screen)
        if overlap.width() >= MIN_VISIBLE_WIDTH and overlap.height() >= MIN_VISIBLE_HEIGHT:
            return True
    return False


def encode_geometry(window: QWidget) -> str:
    """Qt's opaque geometry blob (position, size, maximised state) as ASCII text."""
    return bytes(window.saveGeometry().toBase64()).decode("ascii")


def restore_geometry(window: QWidget, encoded: str | None) -> bool:
    """Apply a saved geometry if it is valid and still on a connected screen."""
    if not encoded:
        return False
    try:
        blob = base64.b64decode(encoded.encode("ascii"), validate=True)
    except (binascii.Error, UnicodeEncodeError, ValueError):
        return False
    if not window.restoreGeometry(QByteArray(blob)):
        return False
    screens = [screen.availableGeometry() for screen in QGuiApplication.screens()]
    return not screens or is_reachable(window.frameGeometry(), screens)
