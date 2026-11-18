"""DraftAutosaver: debounce, coalescing, ordering, failure handling, stale answers, shutdown."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6")

from bank import make_question, seed  # noqa: E402
from PySide6.QtCore import QObject  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.answers import AnswerLetter  # noqa: E402
from vascqbank_desktop.domain.question import QuestionContent  # noqa: E402
from vascqbank_desktop.services.catalog_service import CatalogService  # noqa: E402
from vascqbank_desktop.services.draft_service import DraftSaved, DraftService  # noqa: E402
from vascqbank_desktop.services.edit_errors import StorageFailure, StorageProblem  # noqa: E402
from vascqbank_desktop.ui.autosave import DraftAutosaver, DraftSnapshot, DraftWrite  # noqa: E402

pytestmark = pytest.mark.gui

WAIT_MS = 10_000


def text(stem: str) -> QuestionContent:
    return QuestionContent(stem, ("ა", "ბ", "გ", "დ", "ე"), AnswerLetter.C, "განმარტება")


def snap(session: int, seq: int, stem: str | None = None, question_id: int = 1) -> DraftSnapshot:
    return DraftSnapshot(session, seq, question_id, 1, 0, text(stem or f"ტექსტი {seq}"), "დავით ფარღალავა")


class FakeService:
    """Records every write, can block, fail, and measures how many writes ever ran at once."""

    def __init__(self) -> None:
        self.stems: list[str] = []
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.fail_remaining = 0
        self.active = 0
        self.max_active = 0
        self.threads: set[int] = set()
        self._lock = threading.Lock()

    def save_draft(self, question_id, base_revision_id, base_lock_version, content, reviewer_name, note=None):
        with self._lock:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.threads.add(threading.get_ident())
        try:
            self.entered.set()
            if self.gate is not None:
                assert self.gate.wait(10), "test gate was never released"
            if self.fail_remaining > 0:
                self.fail_remaining -= 1
                raise StorageFailure(StorageProblem.DISK_FULL, "database or disk is full")
            self.stems.append(content.stem)
            return DraftSaved(question_id, "2026-11-17T18:00:00+00:00", len(self.stems))
        finally:
            with self._lock:
                self.active -= 1


@pytest.fixture
def fake() -> FakeService:
    return FakeService()


def make(qtbot, service, **kwargs) -> DraftAutosaver:
    holder = QObject()
    autosaver = DraftAutosaver(service, holder, **{"delay_ms": 40, "retry_ms": 60, **kwargs})
    autosaver._holder = holder  # keep the parent alive for the test
    return autosaver


def collect(autosaver: DraftAutosaver) -> tuple[list[DraftWrite], list[BaseException]]:
    saved: list[DraftWrite] = []
    failed: list[BaseException] = []
    autosaver.draftSaved.connect(saved.append)
    autosaver.writeFailed.connect(failed.append)
    return saved, failed


# ------------------------------------------------------------------ debounce and coalescing


def test_a_burst_of_edits_is_one_write_of_the_newest_text(qtbot, fake) -> None:
    # 1 s idle delay: far longer than any gap between the simulated keystrokes, even on a loaded
    # CI runner where a 10 ms wait can overrun by 100+ ms (seen on Windows with 150 ms).
    autosaver = make(qtbot, fake, delay_ms=1000)
    saved, _failed = collect(autosaver)
    session = autosaver.begin_session()
    for seq in range(1, 8):
        autosaver.schedule(snap(session, seq))
        qtbot.wait(10)  # typing continues: the idle timer keeps restarting
    assert fake.stems == [] and autosaver.pending  # nothing has been written while typing
    qtbot.waitUntil(lambda: bool(saved), timeout=WAIT_MS)
    assert fake.stems == ["ტექსტი 7"] and saved[0].seq == 7
    assert not autosaver.pending and autosaver.confirmed_seq == 7


def test_edits_during_a_write_are_coalesced_into_one_follow_up_with_the_newest_text(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    saved, _failed = collect(autosaver)
    fake.gate = threading.Event()
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1))
    qtbot.waitUntil(fake.entered.is_set, timeout=WAIT_MS)
    for seq in (2, 3, 4):  # typed while write 1 is still blocked
        autosaver.schedule(snap(session, seq))
    qtbot.wait(120)  # idle timer fires; single flight: still only one write running
    assert fake.max_active == 1 and autosaver.writing
    fake.gate.set()
    qtbot.waitUntil(lambda: not autosaver.pending, timeout=WAIT_MS)
    assert fake.stems == ["ტექსტი 1", "ტექსტი 4"]  # texts 2 and 3 were superseded, not written
    assert [w.seq for w in saved] == [1, 4]


def test_saved_is_only_reported_after_the_write_returned(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    saved, _failed = collect(autosaver)
    fake.gate = threading.Event()
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1))
    qtbot.waitUntil(fake.entered.is_set, timeout=WAIT_MS)
    qtbot.wait(100)
    assert saved == [] and autosaver.pending and autosaver.confirmed_seq == 0  # the write has not committed
    fake.gate.set()
    qtbot.waitUntil(lambda: bool(saved), timeout=WAIT_MS)
    assert autosaver.confirmed_seq == 1 and not autosaver.pending


def test_writes_run_off_the_ui_thread(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    saved, _ = collect(autosaver)
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1))
    qtbot.waitUntil(lambda: bool(saved), timeout=WAIT_MS)
    assert threading.get_ident() not in fake.threads


# ------------------------------------------------------------------ flush


def test_flush_writes_immediately_and_confirms(qtbot, fake) -> None:
    autosaver = make(qtbot, fake, delay_ms=60_000)  # the idle timer would never fire in this test
    saved, _ = collect(autosaver)
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1, "ნავიგაციამდე"))
    assert autosaver.pending
    assert autosaver.flush() is True
    assert fake.stems == ["ნავიგაციამდე"] and not autosaver.pending and [w.seq for w in saved] == [1]
    assert threading.get_ident() in fake.threads  # on the calling thread: the caller may rely on it


def test_flush_waits_for_the_write_in_flight_then_writes_the_newest(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    fake.gate = threading.Event()
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1))
    qtbot.waitUntil(fake.entered.is_set, timeout=WAIT_MS)
    autosaver.schedule(snap(session, 2))
    threading.Timer(0.15, fake.gate.set).start()  # the slow write finishes while flush is waiting
    assert autosaver.flush() is True
    assert fake.stems == ["ტექსტი 1", "ტექსტი 2"] and fake.max_active == 1
    assert autosaver.confirmed_seq == 2


def test_flush_with_nothing_pending_is_a_noop(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    assert autosaver.flush() is True and fake.stems == []
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1))
    assert autosaver.flush() and autosaver.flush()
    assert fake.stems == ["ტექსტი 1"]  # the second flush found nothing new


# ------------------------------------------------------------------ failures


def test_a_failed_write_keeps_the_text_pending_reports_it_and_retries(qtbot, fake) -> None:
    autosaver = make(qtbot, fake, retry_ms=80)
    saved, failed = collect(autosaver)
    fake.fail_remaining = 2
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1, "არ უნდა დაიკარგოს"))
    qtbot.waitUntil(lambda: len(failed) >= 1, timeout=WAIT_MS)
    assert isinstance(failed[0], StorageFailure) and failed[0].problem is StorageProblem.DISK_FULL
    assert autosaver.pending and autosaver.last_error is failed[0] and saved == []  # nothing claimed as saved
    qtbot.waitUntil(lambda: bool(saved), timeout=WAIT_MS)  # the retry succeeds once the disk has room
    assert fake.stems == ["არ უნდა დაიკარგოს"] and not autosaver.pending and autosaver.last_error is None
    assert len(failed) == 2


def test_flush_reports_failure_and_keeps_everything_pending(qtbot, fake) -> None:
    autosaver = make(qtbot, fake, delay_ms=60_000, retry_ms=60_000)
    _saved, failed = collect(autosaver)
    fake.fail_remaining = 1
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1))
    assert autosaver.flush() is False  # the caller must keep the text and show an error
    assert autosaver.pending and isinstance(autosaver.last_error, StorageFailure) and len(failed) == 1
    assert autosaver.flush() is True  # later the same text goes through
    assert fake.stems == ["ტექსტი 1"]


# ------------------------------------------------------------------ stale responses and sessions


def test_the_outcome_of_an_earlier_session_never_touches_the_new_one(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    saved, failed = collect(autosaver)
    fake.gate = threading.Event()
    first = autosaver.begin_session()
    autosaver.schedule(snap(first, 1, "პირველი კითხვა", question_id=1))
    qtbot.waitUntil(fake.entered.is_set, timeout=WAIT_MS)

    second = autosaver.begin_session()  # the reviewer moved to another question
    autosaver.schedule(snap(second, 1, "მეორე კითხვა", question_id=2))
    fake.gate.set()
    qtbot.waitUntil(lambda: not autosaver.pending and not autosaver.writing, timeout=WAIT_MS)
    qtbot.wait(100)
    assert fake.stems == ["პირველი კითხვა", "მეორე კითხვა"]  # both were written, one after the other
    assert [w.session_id for w in saved] == [second]  # only the current session was ever reported
    assert failed == [] and fake.max_active == 1


def test_a_snapshot_from_a_dead_session_is_ignored(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    first = autosaver.begin_session()
    autosaver.begin_session()
    autosaver.schedule(snap(first, 1))
    assert not autosaver.pending and autosaver.flush() and fake.stems == []


def test_a_late_failure_of_an_old_session_is_not_reported_to_the_new_one(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    _saved, failed = collect(autosaver)
    fake.gate = threading.Event()
    fake.fail_remaining = 1
    first = autosaver.begin_session()
    autosaver.schedule(snap(first, 1))
    qtbot.waitUntil(fake.entered.is_set, timeout=WAIT_MS)
    autosaver.begin_session()
    fake.gate.set()
    qtbot.waitUntil(lambda: not autosaver.writing, timeout=WAIT_MS)
    qtbot.wait(100)
    assert failed == [] and autosaver.last_error is None and not autosaver.pending


# ------------------------------------------------------------------ shutdown and destruction


def test_close_waits_for_the_running_write_and_persists_the_rest(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    saved, _ = collect(autosaver)
    fake.gate = threading.Event()
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1, "მიმდინარე ჩაწერა"))
    qtbot.waitUntil(fake.entered.is_set, timeout=WAIT_MS)
    autosaver.schedule(snap(session, 2, "ბოლო სიმბოლოები"))
    threading.Timer(0.1, fake.gate.set).start()
    assert autosaver.close() is True and autosaver.is_closed
    assert fake.stems == ["მიმდინარე ჩაწერა", "ბოლო სიმბოლოები"]
    qtbot.wait(100)  # queued signals of the closed autosaver must be ignored without errors
    autosaver.schedule(snap(session, 3, "დახურულის შემდეგ"))
    qtbot.wait(150)
    assert fake.stems == ["მიმდინარე ჩაწერა", "ბოლო სიმბოლოები"]


def test_close_with_a_failing_disk_says_so(qtbot, fake) -> None:
    autosaver = make(qtbot, fake)
    fake.fail_remaining = 5
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1))
    assert (
        autosaver.close() is False and autosaver.pending
    )  # the caller (window close) must not claim success


def test_destroying_the_owner_while_a_write_runs_is_safe(qtbot, fake) -> None:
    holder = QObject()
    autosaver = DraftAutosaver(fake, holder, delay_ms=20)
    fake.gate = threading.Event()
    session = autosaver.begin_session()
    autosaver.schedule(snap(session, 1))
    qtbot.waitUntil(fake.entered.is_set, timeout=WAIT_MS)
    holder.deleteLater()  # e.g. the window is destroyed while the write is still running
    qtbot.wait(50)
    fake.gate.set()
    qtbot.wait(
        200
    )  # the late result finds nobody; pytest-qt fails the test on any exception raised in a slot
    assert fake.stems == ["ტექსტი 1"]  # the write itself still completed


# ------------------------------------------------------------------ serialisation under pressure


def test_no_two_writes_ever_overlap_under_rapid_edits(qtbot, fake) -> None:
    class Slow(FakeService):
        def save_draft(self, *args, **kwargs):
            time.sleep(0.003)
            return super().save_draft(*args, **kwargs)

    slow = Slow()
    autosaver = make(qtbot, slow, delay_ms=1)
    session = autosaver.begin_session()
    for seq in range(1, 150):
        autosaver.schedule(snap(session, seq))
        if seq % 10 == 0:
            qtbot.wait(2)
    assert autosaver.flush() is True
    assert slow.max_active == 1  # strictly serial
    assert slow.stems[-1] == "ტექსტი 149" and len(slow.stems) < 149  # newest last; most were coalesced


@pytest.fixture
def real(app_paths: AppPaths, styled_qapp):
    seed(app_paths, [make_question("ALI", n) for n in range(1, 4)])
    return app_paths, DraftService(app_paths), CatalogService(app_paths)


def test_real_database_rapid_edits_end_with_exactly_the_last_text(qtbot, real) -> None:
    _paths, service, catalog = real
    detail = catalog.detail_by_uid("ALI-0002")
    autosaver = make(qtbot, service, delay_ms=1)
    saved, failed = collect(autosaver)
    session = autosaver.begin_session()
    for seq in range(1, 201):
        autosaver.schedule(
            DraftSnapshot(
                session,
                seq,
                detail.id,
                detail.revision_id,
                detail.lock_version,
                text(f"ქართული ტექსტი №{seq} ა-ჰ"),
                "დავით ფარღალავა",
            )
        )
        if seq % 25 == 0:
            qtbot.wait(3)
    assert autosaver.flush() is True
    stored = service.load_draft(detail.id)
    assert stored is not None and stored.content.stem == "ქართული ტექსტი №200 ა-ჰ"
    assert failed == [] and 1 <= stored.write_count < 200  # worker threads used their own connections
    assert autosaver.confirmed_seq == 200
