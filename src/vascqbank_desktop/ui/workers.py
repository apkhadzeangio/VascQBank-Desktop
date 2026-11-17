"""Run a blocking service call on a background thread.

Services open their own SQLite connection per call, so running one on a
worker thread is safe. Results come back to the UI thread through queued
Qt signals.

* :class:`BackgroundJob` — one call on a worker-pool thread; optionally reports
  progress while it runs.
* :class:`LatestOnlyRunner` — for requests that supersede each other (search
  as you type): calls run one at a time and only the newest waiting request is
  kept, so a burst of keystrokes never queues a pile of obsolete searches.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

log = logging.getLogger(__name__)


class _Signals(QObject):
    """Lives in the UI thread; worker threads only *emit* its signals (queued to the UI thread)."""

    succeeded = Signal(object)
    failed = Signal(object)
    progress = Signal(object)


# Runnables that have been started and not yet finished. The pool only holds a raw pointer,
# so Python must keep them alive until run() has returned, whatever happens to their job.
_RUNNING: set[_Runnable] = set()


class _Runnable(QRunnable):
    def __init__(self, fn: Callable[..., Any], signals: _Signals, report_progress: bool) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self._fn = fn
        self._signals = signals
        self._report_progress = report_progress
        self.finished = threading.Event()

    def run(self) -> None:
        try:
            try:
                result = self._fn(self._signals.progress.emit) if self._report_progress else self._fn()
            except Exception as exc:  # reported to the UI, never swallowed
                # Logged here as well: a job whose creator did not connect `failed` must still
                # leave a trace (the traceback is not available once it crosses the thread).
                log.exception("Background call failed: %s", exc)
                self._signals.failed.emit(exc)
            else:
                self._signals.succeeded.emit(result)
        finally:
            self.finished.set()
            _RUNNING.discard(self)


class BackgroundJob(QObject):
    """One call on a Qt worker-pool thread.

    Uses :class:`QThreadPool` and :class:`QRunnable` — Qt's recommended way to run a function in
    the background — rather than a per-job ``QThread`` with an object moved into it: there is no
    thread to create, quit and destroy per call, and no object living in a thread that Python
    may free at an arbitrary moment.

    With ``report_progress=True`` the function receives a callable and may call it with any object
    at any time; each object arrives on :attr:`progress` in the UI thread.
    """

    succeeded = Signal(object)
    failed = Signal(object)
    progress = Signal(object)

    def __init__(
        self,
        fn: Callable[..., Any],
        parent: QObject | None = None,
        *,
        report_progress: bool = False,
    ) -> None:
        super().__init__(parent)
        self._signals = _Signals()
        self._signals.succeeded.connect(self.succeeded)
        self._signals.failed.connect(self.failed)
        self._signals.progress.connect(self.progress)
        self._runnable = _Runnable(fn, self._signals, report_progress)
        self._started = False

    def start(self) -> None:
        self._started = True
        _RUNNING.add(self._runnable)
        QThreadPool.globalInstance().start(self._runnable)

    def is_running(self) -> bool:
        return self._started and not self._runnable.finished.is_set()

    def wait(self, ms: int = 30_000) -> bool:
        """Block until the function has returned (its result signal may still be queued)."""
        return not self._started or self._runnable.finished.wait(ms / 1000)

    def stop_and_wait(self, ms: int = 30_000) -> bool:
        """Let the running call finish before shutting down. There is nothing to stop: the call
        always runs to its end, so this is :meth:`wait`; it exists for explicit shutdown code."""
        return self.wait(ms)


_Handler = Callable[[Any], None]


class LatestOnlyRunner(QObject):
    """Serialises background calls; a newer waiting request replaces an older one.

    The call that is already running is never interrupted (SQLite queries are
    short); its result is still delivered, so callers must ignore results that
    are no longer wanted — typically with a generation counter.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._job: BackgroundJob | None = None
        self._pending: tuple[Callable[[], Any], _Handler, _Handler | None] | None = None
        self._closed = False

    def is_busy(self) -> bool:
        return self._job is not None

    def submit(
        self,
        fn: Callable[[], Any],
        on_success: _Handler,
        on_failure: _Handler | None = None,
    ) -> None:
        if self._closed:
            return
        if self._job is not None:
            self._pending = (fn, on_success, on_failure)
            return
        self._start(fn, on_success, on_failure)

    def _start(self, fn: Callable[[], Any], on_success: _Handler, on_failure: _Handler | None) -> None:
        job = BackgroundJob(fn, self)
        self._job = job
        job.succeeded.connect(lambda value: self._finished(job, on_success, value))
        job.failed.connect(lambda error: self._finished(job, on_failure, error))
        job.start()

    def _finished(self, job: BackgroundJob, handler: _Handler | None, value: object) -> None:
        if self._closed:
            return
        self._job = None
        job.deleteLater()
        pending, self._pending = self._pending, None
        if handler is not None:
            try:
                handler(value)
            except Exception:  # a faulty handler must not stop later requests
                log.exception("Background result handler failed")
        if pending is not None and not self._closed:
            self._start(*pending)

    def shutdown(self, wait_ms: int = 30_000) -> None:
        """Drop waiting requests and wait for the running one; no handler runs afterwards."""
        self._closed = True
        self._pending = None
        job, self._job = self._job, None
        if job is not None:
            job.stop_and_wait(wait_ms)
