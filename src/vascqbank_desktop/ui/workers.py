"""Run a blocking service call on a background thread.

Services open their own SQLite connection per call, so running one on a
worker thread is safe. Results come back to the UI thread through queued
Qt signals.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot


class _Task(QObject):
    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self._fn = fn

    @Slot()
    def run(self) -> None:
        try:
            result = self._fn()
        except Exception as exc:  # reported to the UI, never swallowed
            self.failed.emit(exc)
        else:
            self.succeeded.emit(result)


class BackgroundJob(QObject):
    """Owns one worker thread for one call."""

    succeeded = Signal(object)
    failed = Signal(object)

    def __init__(self, fn: Callable[[], Any], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread = QThread()
        self._task = _Task(fn)
        self._task.moveToThread(self._thread)
        self._thread.started.connect(self._task.run)
        self._task.succeeded.connect(self._on_succeeded)
        self._task.failed.connect(self._on_failed)

    def start(self) -> None:
        self._thread.start()

    def is_running(self) -> bool:
        return self._thread.isRunning()

    def wait(self, ms: int = 30_000) -> bool:
        return self._thread.wait(ms)

    def _finish(self) -> None:
        self._thread.quit()
        self._thread.wait()

    @Slot(object)
    def _on_succeeded(self, result: object) -> None:
        self._finish()
        self.succeeded.emit(result)

    @Slot(object)
    def _on_failed(self, exc: object) -> None:
        self._finish()
        self.failed.emit(exc)
