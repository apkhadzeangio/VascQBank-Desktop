"""Background job infrastructure: results come back on the UI thread, requests supersede each other."""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QCoreApplication, QThread  # noqa: E402

from vascqbank_desktop.ui.workers import BackgroundJob, LatestOnlyRunner  # noqa: E402

pytestmark = pytest.mark.gui

WAIT_MS = 10_000


def on_ui_thread() -> bool:
    return QThread.currentThread() == QCoreApplication.instance().thread()


def test_the_function_runs_off_the_ui_thread_and_the_result_returns_on_it(qtbot, styled_qapp) -> None:
    ran_on_ui: list[bool] = []
    delivered_on_ui: list[bool] = []
    delivered: list[object] = []
    job = BackgroundJob(lambda: (ran_on_ui.append(on_ui_thread()), {"answer": 42})[1])

    def receive(value: object) -> None:
        delivered_on_ui.append(on_ui_thread())
        delivered.append(value)

    job.succeeded.connect(receive)
    job.start()
    qtbot.waitUntil(lambda: bool(delivered), timeout=WAIT_MS)
    assert delivered == [{"answer": 42}]
    assert ran_on_ui == [False]  # the call did not run on the UI thread
    assert delivered_on_ui == [True]  # but its result is delivered on it


def test_an_exception_is_reported_not_swallowed(qtbot, styled_qapp) -> None:
    errors: list[object] = []
    job = BackgroundJob(lambda: 1 / 0)
    job.failed.connect(errors.append)
    job.start()
    qtbot.waitUntil(lambda: bool(errors), timeout=WAIT_MS)
    assert isinstance(errors[0], ZeroDivisionError)


def test_progress_arrives_in_order_before_the_result(qtbot, styled_qapp) -> None:
    events: list[tuple[str, object]] = []

    def work(report) -> str:
        for step in range(5):
            report(step)
        return "done"

    job = BackgroundJob(work, report_progress=True)
    job.progress.connect(lambda step: events.append(("progress", step)))
    job.succeeded.connect(lambda value: events.append(("done", value)))
    job.start()
    qtbot.waitUntil(lambda: ("done", "done") in events, timeout=WAIT_MS)
    assert events == [("progress", n) for n in range(5)] + [("done", "done")]


def test_wait_and_state(qtbot, styled_qapp) -> None:
    release = threading.Event()
    job = BackgroundJob(lambda: release.wait(5))
    assert not job.is_running() and job.wait(10)  # never started: nothing to wait for
    job.start()
    assert job.is_running() and not job.wait(50)  # still blocked
    release.set()
    assert job.wait(5000) and not job.is_running()


def test_stop_and_wait_lets_the_call_finish(qtbot, styled_qapp) -> None:
    finished: list[bool] = []
    job = BackgroundJob(lambda: (time.sleep(0.15), finished.append(True)))
    job.start()
    assert job.stop_and_wait(5000)
    assert finished == [True]  # shutdown code never abandons a half-finished call


def test_a_dropped_job_object_does_not_crash_a_running_call(qtbot, styled_qapp) -> None:
    done = threading.Event()
    job = BackgroundJob(lambda: (time.sleep(0.1), done.set()))
    job.start()
    del job  # the caller lost interest while the call is still running
    assert done.wait(5)
    qtbot.wait(50)  # the late result signal must find nobody and do no harm


def test_many_jobs_in_a_row_are_all_delivered(qtbot, styled_qapp) -> None:
    results: list[int] = []
    jobs = []
    for n in range(200):
        job = BackgroundJob(lambda n=n: [n] * 500)  # real object payloads cross the thread boundary
        job.succeeded.connect(lambda value: results.append(len(value)))
        jobs.append(job)
        job.start()
    qtbot.waitUntil(lambda: len(results) == 200, timeout=WAIT_MS)
    assert results == [500] * 200


# ------------------------------------------------------------------ LatestOnlyRunner


def test_runner_runs_calls_one_at_a_time_and_keeps_only_the_newest_waiting(qtbot, styled_qapp) -> None:
    runner = LatestOnlyRunner()
    release = threading.Event()
    executed: list[str] = []
    delivered: list[str] = []

    def call(name: str, block: bool = False):
        def run() -> str:
            executed.append(name)
            if block:
                release.wait(5)
            return name

        return run

    runner.submit(call("first", block=True), delivered.append)
    qtbot.waitUntil(lambda: executed == ["first"], timeout=WAIT_MS)
    runner.submit(call("second"), delivered.append)
    runner.submit(call("third"), delivered.append)  # replaces "second" before it ever starts
    assert runner.is_busy()
    release.set()
    qtbot.waitUntil(lambda: not runner.is_busy(), timeout=WAIT_MS)
    assert executed == ["first", "third"] and delivered == ["first", "third"]
    runner.shutdown()


def test_a_failing_handler_does_not_stop_later_requests(qtbot, styled_qapp) -> None:
    runner = LatestOnlyRunner()
    delivered: list[str] = []

    def explode(_value: object) -> None:
        raise RuntimeError("handler bug")

    runner.submit(lambda: "one", explode)
    qtbot.waitUntil(lambda: not runner.is_busy(), timeout=WAIT_MS)
    runner.submit(lambda: "two", delivered.append)
    qtbot.waitUntil(lambda: delivered == ["two"], timeout=WAIT_MS)
    runner.shutdown()


def test_a_failing_call_reaches_the_failure_handler(qtbot, styled_qapp) -> None:
    runner = LatestOnlyRunner()
    errors: list[object] = []
    runner.submit(lambda: 1 / 0, lambda _v: None, errors.append)
    qtbot.waitUntil(lambda: bool(errors), timeout=WAIT_MS)
    assert isinstance(errors[0], ZeroDivisionError)
    runner.shutdown()


def test_shutdown_waits_for_the_running_call_and_drops_the_rest(qtbot, styled_qapp) -> None:
    runner = LatestOnlyRunner()
    started = threading.Event()
    finished = threading.Event()
    delivered: list[str] = []

    def slow() -> str:
        started.set()
        time.sleep(0.2)
        finished.set()
        return "slow"

    runner.submit(slow, delivered.append)
    assert started.wait(5)
    runner.submit(lambda: "never", delivered.append)
    runner.shutdown()
    assert finished.is_set()  # it waited for the call that was already running
    qtbot.wait(100)
    assert delivered == []  # nothing is delivered after shutdown, and the waiting request never ran
    runner.submit(lambda: "late", delivered.append)  # a closed runner ignores new work
    qtbot.wait(50)
    assert delivered == [] and not runner.is_busy()
