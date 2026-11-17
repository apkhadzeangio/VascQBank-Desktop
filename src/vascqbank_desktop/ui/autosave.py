"""Debounced, crash-safe autosave of the editor's draft.

Guarantees (each one is covered by a test):

* **"Saved" means committed.** A write is reported as done only after
  :meth:`DraftService.save_draft` has returned, i.e. after the SQLite transaction committed.
  While a write is pending or failed, :attr:`DraftAutosaver.pending` stays true.
* **One write at a time.** At most one background write is in flight; edits made meanwhile
  are coalesced into a single follow-up write of the *newest* text (older ones are dropped
  because the newest contains them). Together with the service's lock this serialises all
  draft writes, and no database connection is ever shared between threads (the service opens
  one per call).
* **Stale answers are ignored.** Every snapshot carries a session id and a sequence number; the
  outcome of a write belonging to an earlier session, or to text that has since been replaced,
  never changes the state of a newer editing session.
* **Nothing is dropped on failure.** A failed write keeps the text pending, reports the error
  (``writeFailed``) and retries after a pause; :meth:`flush` reports failure to its caller.
* **Orderly shutdown.** :meth:`flush` / :meth:`close` wait for the write in flight, then write
  the newest pending text synchronously, and only then report success. Late signals of a closed
  or destroyed autosaver are ignored.

Timing: a write starts :data:`DEFAULT_DELAY_MS` (≈1 s) after the last keystroke.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from PySide6.QtCore import QObject, QTimer, Signal

from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.services.draft_service import DraftSaved, DraftService
from vascqbank_desktop.ui.workers import BackgroundJob

log = logging.getLogger(__name__)

DEFAULT_DELAY_MS = 1000
DEFAULT_RETRY_MS = 3000


@dataclass(frozen=True, slots=True)
class DraftSnapshot:
    """Everything one draft write needs; immutable, so a write always stores one exact state."""

    session_id: int
    seq: int
    question_id: int
    base_revision_id: int
    base_lock_version: int
    content: QuestionContent
    reviewer_name: str
    note: str | None = None


@dataclass(frozen=True, slots=True)
class DraftWrite:
    """A confirmed (committed) draft write."""

    session_id: int
    seq: int
    saved: DraftSaved


@dataclass(slots=True)
class _Ticket:
    """Carries one write's outcome from the worker thread to whoever needs it first: the queued
    signal handler, or :meth:`DraftAutosaver.flush` (which cannot wait for queued signals)."""

    snapshot: DraftSnapshot
    saved: DraftSaved | None = None
    error: BaseException | None = None
    applied: bool = False


class DraftAutosaver(QObject):
    draftSaved = Signal(object)  # DraftWrite — only after the commit, only for the current session
    writeFailed = Signal(object)  # the exception; the text stays pending and a retry is scheduled

    def __init__(
        self,
        service: DraftService,
        parent: QObject | None = None,
        *,
        delay_ms: int = DEFAULT_DELAY_MS,
        retry_ms: int = DEFAULT_RETRY_MS,
    ) -> None:
        super().__init__(parent)
        self._service = service
        self._session = 0
        self._latest: DraftSnapshot | None = None
        self._confirmed_seq = 0
        self._last_error: BaseException | None = None
        self._inflight: tuple[BackgroundJob, _Ticket, threading.Event] | None = None
        self._closed = False
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(delay_ms)
        self._timer.timeout.connect(self._kick)
        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.setInterval(retry_ms)
        self._retry.timeout.connect(self._kick)

    # ------------------------------------------------------------------ state
    @property
    def session_id(self) -> int:
        return self._session

    @property
    def pending(self) -> bool:
        """The newest text of the current session is not yet confirmed in the database."""
        return self._latest is not None and self._latest.seq > self._confirmed_seq

    @property
    def writing(self) -> bool:
        return self._inflight is not None

    @property
    def last_error(self) -> BaseException | None:
        """The error of the latest failed write of this session (cleared by the next success)."""
        return self._last_error

    @property
    def latest_seq(self) -> int:
        return self._latest.seq if self._latest is not None else 0

    @property
    def confirmed_seq(self) -> int:
        return self._confirmed_seq

    @property
    def is_closed(self) -> bool:
        return self._closed

    # ------------------------------------------------------------------ sessions
    def begin_session(self) -> int:
        """Start a new editing session; whatever the previous one still had pending is forgotten
        (callers :meth:`flush` first) and the outcome of its in-flight write is ignored."""
        self._session += 1
        self._latest = None
        self._confirmed_seq = 0
        self._last_error = None
        self._timer.stop()
        self._retry.stop()
        return self._session

    def end_session(self) -> None:
        self.begin_session()

    # ------------------------------------------------------------------ scheduling
    def schedule(self, snapshot: DraftSnapshot) -> None:
        """Remember the newest text and (re)start the idle timer."""
        if self._closed or snapshot.session_id != self._session:
            return
        self._latest = snapshot
        self._retry.stop()
        self._timer.start()

    def _kick(self) -> None:
        """Start a write of the newest pending text, unless one is already running."""
        if self._closed:
            return
        snapshot = self._latest
        if snapshot is None or snapshot.session_id != self._session or snapshot.seq <= self._confirmed_seq:
            return
        if self._inflight is not None:
            return  # single flight: the follow-up is started when the running write completes
        ticket = _Ticket(snapshot)
        done = threading.Event()

        def write() -> None:
            try:
                ticket.saved = self._write(snapshot)
            except BaseException as exc:  # noqa: BLE001 - delivered through the ticket, never lost
                ticket.error = exc
                raise
            finally:
                done.set()

        job = BackgroundJob(write, self)
        job.succeeded.connect(lambda _result: self._on_outcome(ticket))
        job.failed.connect(lambda _error: self._on_outcome(ticket))
        self._inflight = (job, ticket, done)
        job.start()

    def _write(self, snapshot: DraftSnapshot) -> DraftSaved:
        return self._service.save_draft(
            snapshot.question_id,
            snapshot.base_revision_id,
            snapshot.base_lock_version,
            snapshot.content,
            snapshot.reviewer_name,
            snapshot.note,
        )

    # ------------------------------------------------------------------ outcomes
    def _on_outcome(self, ticket: _Ticket) -> None:
        """Queued handler (UI thread) for a finished background write."""
        inflight = self._inflight
        if inflight is not None and inflight[1] is ticket:
            self._inflight = None
            inflight[0].deleteLater()
        if self._closed:
            return
        self._apply(ticket)
        # Text that arrived while this write ran gets its own write — unless typing continues
        # (the idle timer will fire) or a retry is already waiting.
        if self.pending and not self._timer.isActive() and not self._retry.isActive():
            self._kick()

    def _apply(self, ticket: _Ticket) -> None:
        """Fold one outcome into the state, exactly once, and only if it is still relevant."""
        if ticket.applied:
            return
        ticket.applied = True
        snapshot = ticket.snapshot
        if snapshot.session_id != self._session:
            return  # an earlier session's write: it committed, but it is not this session's state
        if ticket.saved is not None:
            self._confirmed_seq = max(self._confirmed_seq, snapshot.seq)
            self._last_error = None
            self.draftSaved.emit(DraftWrite(snapshot.session_id, snapshot.seq, ticket.saved))
        else:
            error = ticket.error or RuntimeError("draft write failed")
            self._last_error = error
            self.writeFailed.emit(error)
            if self.pending:
                self._retry.start()

    # ------------------------------------------------------------------ synchronous flush
    def flush(self, timeout_s: float = 15.0) -> bool:
        """Persist everything pending **now** and return whether it is confirmed.

        Waits for the write in flight, then writes the newest text on the calling thread. Use
        before navigating away, leaving edit mode, switching subjects, saving and closing. On
        failure it returns ``False``, keeps the text pending and records :attr:`last_error` —
        the caller must then keep the text and tell the reviewer.
        """
        self._timer.stop()
        self._retry.stop()
        deadline = time.monotonic() + timeout_s
        inflight = self._inflight
        if inflight is not None:
            job, ticket, done = inflight
            if not done.wait(max(0.0, deadline - time.monotonic())):
                return False  # still running: nothing may be claimed as saved
            job.wait(1000)
            self._inflight = None
            self._apply(ticket)  # the outcome is read from the ticket, not from a queued signal
            job.deleteLater()
        snapshot = self._latest
        if snapshot is None or snapshot.session_id != self._session or snapshot.seq <= self._confirmed_seq:
            return not self.pending
        try:
            saved = self._write(snapshot)
        except BaseException as exc:  # noqa: BLE001 - recorded and reported to the caller
            self._last_error = exc
            log.warning("Draft flush failed: %s", exc)
            self.writeFailed.emit(exc)
            return False
        self._confirmed_seq = max(self._confirmed_seq, snapshot.seq)
        self._last_error = None
        self.draftSaved.emit(DraftWrite(snapshot.session_id, snapshot.seq, saved))
        return not self.pending

    def close(self, timeout_s: float = 15.0) -> bool:
        """Final flush, then ignore everything that arrives later. Returns the flush result."""
        confirmed = self.flush(timeout_s)
        self._closed = True
        self._timer.stop()
        self._retry.stop()
        return confirmed
