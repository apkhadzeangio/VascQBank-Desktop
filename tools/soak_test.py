"""Long-running GUI soak test on a SYNTHETIC bank (artificial text, never real questions).

    QT_QPA_PLATFORM=offscreen python tools/soak_test.py --minutes 15 --questions 2000 \\
        --seed 9 --output soak-report.json

Builds a throw-away data folder (``--workdir`` or a temporary one), imports a synthetic partial
bank through the real importer, opens the real :class:`MainWindow` and then, until the time is up,
performs a seeded random mix of reviewer actions through the same controller methods the buttons
and shortcuts call:

browse next/previous, go to a question, switch subject, quick filters, search, edit + autosave +
save, approve-and-next, reject, reopen, revision history, manual backups and small exports.

Every few seconds it samples resident memory, open file descriptors / handles, OS threads, Qt
thread-pool activity, Python object count (and, with ``--tracemalloc``, traced Python memory). It
also measures how long each action takes and — the responsiveness figure — how late a 50 ms
heartbeat timer fires over the whole run (a long synchronous step on the GUI thread shows up
there). Any unexpected modal dialog is closed and recorded; any exception (including ones raised
inside Qt slots) and any ERROR log record counts as a failure.

At the end the window is closed the normal way, the thread pool must drain, and the database must
pass ``integrity_check`` and ``foreign_key_check``. The JSON report holds every sample and a summary;
the exit status is 0 only if no failure was recorded. Nothing here talks to the network.

The numbers describe the machine and platform the run happened on — nothing more.
"""

from __future__ import annotations

import argparse
import gc
import json
import logging
import os
import platform
import random
import sqlite3
import statistics
import sys
import tempfile
import time
import traceback
import tracemalloc
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from synthetic import write_pair  # noqa: E402

from vascqbank_desktop import __version__  # noqa: E402
from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services import import_service  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402

REVIEWER = "სინთეზური რეცენზენტი"
SEARCH_TERMS = ("სატესტო ტექსტი", "ვარიანტი ბ", "ALI-1", "VENOUS", "სინთეზური", "№12", "განმარტებაა")
BACKUP_EVERY_S = 120.0
EXPORT_EVERY_S = 150.0
SAMPLE_EVERY_S = 5.0
WAIT_MS = 60_000


# ------------------------------------------------------------------------------- process metrics
def rss_kb() -> int | None:
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1])
    except OSError:
        pass
    try:
        import psutil

        return psutil.Process().memory_info().rss // 1024
    except Exception:  # noqa: BLE001 - optional dependency, any platform
        return None


def open_handles() -> int | None:
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        pass
    try:
        import psutil

        proc = psutil.Process()
        return proc.num_handles() if hasattr(proc, "num_handles") else proc.num_fds()
    except Exception:  # noqa: BLE001
        return None


def os_threads() -> int | None:
    try:
        return len(os.listdir("/proc/self/task"))
    except OSError:
        pass
    try:
        import psutil

        return psutil.Process().num_threads()
    except Exception:  # noqa: BLE001
        return None


# ------------------------------------------------------------------------------------ the bank
def build_bank(workdir: Path, questions: int) -> AppPaths:
    per_subject = max(1, -(-questions // len(SUBJECTS)))
    source = workdir / "synthetic-source"
    total = 0
    for subject in SUBJECTS:
        count = min(per_subject, subject.target_count, questions - total)
        if count <= 0:
            break
        write_pair(source, subject.code, range(1, count + 1))
        total += count
    paths = AppPaths(data_dir=workdir / "data")
    initialize_storage(paths)
    report = import_service.dry_run(paths, [source])
    result = import_service.commit_import(paths, report)
    ReviewerProfile(SettingsStore(paths.settings_file)).set_name(REVIEWER)
    print(f"synthetic bank: {len(result.created)} questions in {paths.data_dir}", flush=True)
    return paths


class ErrorLog(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.ERROR)
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(f"{record.name}: {record.getMessage()}"[:500])


# ------------------------------------------------------------------------------------ the run
def run(args: argparse.Namespace) -> dict:  # noqa: PLR0915 - one linear script
    from PySide6.QtCore import QThreadPool, QTimer
    from PySide6.QtTest import QTest
    from PySide6.QtWidgets import QApplication

    from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts
    from vascqbank_desktop.ui.localization import install_translations
    from vascqbank_desktop.ui.main_window import MainWindow
    from vascqbank_desktop.ui.sidebar import PAGE_BACKUPS, PAGE_CATALOG, PAGE_EXPORT
    from vascqbank_desktop.ui.theme import apply_theme

    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="vqb-soak-"))
    workdir.mkdir(parents=True, exist_ok=True)
    paths = build_bank(workdir, args.questions)
    rng = random.Random(args.seed)

    errors: list[str] = []
    dialogs: list[str] = []
    error_log = ErrorLog()
    logging.getLogger().addHandler(error_log)

    def excepthook(kind, value, tb) -> None:  # noqa: ANN001
        errors.append("".join(traceback.format_exception(kind, value, tb))[-2000:])

    sys.excepthook = excepthook

    app = QApplication.instance() or QApplication([])
    register_bundled_fonts()
    app.setFont(build_ui_font())
    apply_theme(app)
    install_translations(app)

    if args.tracemalloc:
        tracemalloc.start()

    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=300)
    window.resize(1400, 900)
    window.show()
    window.show_page(PAGE_CATALOG)
    window.wait_for_background_work()
    view = window.catalog_view

    # A modal dialog would stop an unattended run: close it and record it as unexpected.
    def close_dialogs() -> None:
        widget = app.activeModalWidget()
        if widget is not None:
            text = getattr(widget, "text", lambda: "")()
            dialogs.append(f"{type(widget).__name__}: {widget.windowTitle()} — {text}"[:300])
            widget.close()

    watchdog = QTimer()
    watchdog.timeout.connect(close_dialogs)
    watchdog.start(200)

    heartbeat_gaps: list[float] = []
    last_beat = [time.perf_counter()]

    def beat() -> None:
        now = time.perf_counter()
        heartbeat_gaps.append((now - last_beat[0]) * 1000 - 50)
        last_beat[0] = now

    heartbeat = QTimer()
    heartbeat.timeout.connect(beat)
    heartbeat.start(50)

    def settle(condition: Callable[[], bool] = lambda: True, timeout_ms: int = WAIT_MS) -> bool:
        deadline = time.perf_counter() + timeout_ms / 1000
        QTest.qWait(5)
        while not condition():
            if time.perf_counter() > deadline:
                return False
            QTest.qWait(10)
        return True

    def busy() -> bool:
        return view.is_busy() or window.backup_view.is_busy() or window.export_view.is_running()

    uids: list[str] = []
    conn = sqlite3.connect(paths.database_file)
    try:
        uids = [row[0] for row in conn.execute("SELECT question_uid FROM questions ORDER BY id")]
    finally:
        conn.close()
    codes = sorted({uid.rsplit("-", 1)[0] for uid in uids})
    exports_dir = workdir / "exports"
    edit_counter = [0]
    # the first backup and export happen about 30 s into the run, then at their regular interval
    timers = {
        "backup": time.monotonic() - BACKUP_EVERY_S + 30,
        "export": time.monotonic() - EXPORT_EVERY_S + 30,
    }

    # ---------------------------------------------------------------- the actions
    def op_next() -> object:
        view.next_question()
        return True

    def op_previous() -> object:
        view.previous_question()
        return True

    def op_goto() -> object:
        return view.go_to(rng.choice(uids))

    def op_subject() -> object:
        window.open_subject(rng.choice([*codes, None]))
        return settle(lambda: not view.is_busy())

    def op_quick_filter() -> object:
        view.set_quick_filter(rng.choice([0, 0, 0, 1, 2, 3, 4, 5]))
        return settle(lambda: not view.is_busy())

    def op_search() -> object:
        view.set_search_text(rng.choice(SEARCH_TERMS))
        ok = settle(lambda: not view.is_busy())
        view.set_search_text("")
        return ok and settle(lambda: not view.is_busy())

    def op_edit() -> object:
        current = view.current_question_id
        if current is None:
            return "skipped"
        detail = window.catalog_service.detail(current)
        # Editing an approved question asks for confirmation (the approval would be revoked): a
        # deliberate dialog, not an unattended action — such questions are left to reopen/reject.
        if detail is None or detail.status is ReviewStatus.APPROVED or not view.start_editing():
            return "skipped"
        editor = view.editing.editor
        edit_counter[0] += 1
        editor.stem_edit.setPlainText(
            f"სინთეზური ხანგრძლივი ტესტის ცვლილება №{edit_counter[0]}: რომელია სწორი?"
        )
        if rng.random() < 0.5:
            QTest.qWait(450)  # let the autosaver write a draft first
        editor.save()
        return view.leave_editing()

    def op_approve() -> object:
        workflow = view.workflow
        return workflow.approve_and_next() if workflow is not None else "skipped"

    def op_reject() -> object:
        workflow = view.workflow
        if workflow is None:
            return "skipped"
        workflow.ask_reject_reason = lambda **_kw: "სინთეზური მიზეზი ხანგრძლივი ტესტისთვის"
        return workflow.reject()

    def op_reopen() -> object:
        workflow = view.workflow
        if workflow is None:
            return "skipped"
        workflow.ask_reopen_note = lambda _status: ""
        return workflow.reopen()

    def op_history() -> object:
        if not view.show_history():
            return "skipped"
        QTest.qWait(30)
        view.editing.close_history()
        return True

    def op_backup() -> object:
        if time.monotonic() - timers["backup"] < BACKUP_EVERY_S:
            return "skipped"
        timers["backup"] = time.monotonic()
        window.show_page(PAGE_BACKUPS)
        window.backup_view.create_backup()
        ok = settle(lambda: not window.backup_view.is_busy())
        window.show_page(PAGE_CATALOG)
        return ok

    def op_export() -> object:
        if time.monotonic() - timers["export"] < EXPORT_EVERY_S:
            return "skipped"
        timers["export"] = time.monotonic()
        export = window.export_view
        window.show_page(PAGE_EXPORT)
        export.select_subjects([rng.choice(codes)])
        export.destination_edit.setText(str(exports_dir))
        export.request_preview()
        settle(lambda: not export.is_previewing())
        started = export.start_export()
        ok = settle(lambda: not export.is_running()) if started else "skipped"
        window.show_page(PAGE_CATALOG)
        return ok

    actions: list[tuple[str, Callable[[], object], int]] = [
        ("next", op_next, 20),
        ("previous", op_previous, 6),
        ("goto", op_goto, 10),
        ("subject", op_subject, 5),
        ("quick_filter", op_quick_filter, 6),
        ("search", op_search, 6),
        ("edit_save", op_edit, 10),
        ("approve_next", op_approve, 8),
        ("reject", op_reject, 3),
        ("reopen", op_reopen, 3),
        ("history", op_history, 5),
        ("backup", op_backup, 2),
        ("export", op_export, 2),
    ]
    names = [a[0] for a in actions]
    weights = [a[2] for a in actions]
    table = {a[0]: a[1] for a in actions}

    counts: Counter[str] = Counter()
    skipped: Counter[str] = Counter()
    failed: Counter[str] = Counter()
    durations: dict[str, list[float]] = {name: [] for name in names}
    samples: list[dict] = []
    started = time.monotonic()
    deadline = started + args.minutes * 60
    next_sample = started

    def sample() -> None:
        gc.collect()
        traced = tracemalloc.get_traced_memory() if tracemalloc.is_tracing() else (None, None)
        samples.append(
            {
                "t_s": round(time.monotonic() - started, 1),
                "rss_kb": rss_kb(),
                "open_handles": open_handles(),
                "os_threads": os_threads(),
                "qt_pool_active": QThreadPool.globalInstance().activeThreadCount(),
                "python_objects": len(gc.get_objects()),
                "traced_kb": None if traced[0] is None else traced[0] // 1024,
                "actions": sum(counts.values()),
            }
        )

    while time.monotonic() < deadline:
        if time.monotonic() >= next_sample:
            sample()
            next_sample += SAMPLE_EVERY_S
        name = rng.choices(names, weights)[0]
        mark = time.perf_counter()
        try:
            outcome = table[name]()
        except Exception:  # noqa: BLE001 - the soak test records every failure
            errors.append(f"{name}: {traceback.format_exc()[-2000:]}")
            outcome = False
        durations[name].append((time.perf_counter() - mark) * 1000)
        counts[name] += 1
        if outcome == "skipped":
            skipped[name] += 1
        elif outcome is False:
            failed[name] += 1
        if not settle(lambda: not busy()):
            errors.append(f"{name}: background work did not finish within {WAIT_MS} ms")
    sample()

    # ---------------------------------------------------------------- orderly shutdown
    heartbeat.stop()
    watchdog.stop()
    QTimer.singleShot(0, close_dialogs)
    closed = window.close()
    QTest.qWait(50)
    pool_drained = QThreadPool.globalInstance().waitForDone(30_000)
    conn = sqlite3.connect(paths.database_file)
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        fk_problems = len(conn.execute("PRAGMA foreign_key_check").fetchall())
        status_counts = dict(
            conn.execute("SELECT status, COUNT(*) FROM questions GROUP BY status").fetchall()
        )
        revisions = conn.execute("SELECT COUNT(*) FROM question_revisions").fetchone()[0]
        drafts = conn.execute("SELECT COUNT(*) FROM drafts").fetchone()[0]
    finally:
        conn.close()

    rss = [s["rss_kb"] for s in samples if s["rss_kb"] is not None]
    handles = [s["open_handles"] for s in samples if s["open_handles"] is not None]
    threads = [s["os_threads"] for s in samples if s["os_threads"] is not None]
    warm = max(1, len(samples) // 10)  # skip the warm-up (caches, fonts, first queries)

    def growth(values: list[int]) -> int | None:
        return values[-1] - values[min(warm, len(values) - 1)] if values else None

    def percentile(values: list[float], q: float) -> float:
        return round(statistics.quantiles(values, n=100)[int(q) - 1], 1) if len(values) >= 2 else 0.0

    all_durations = [d for values in durations.values() for d in values]
    failures = errors + [f"log: {r}" for r in error_log.records] + [f"dialog: {d}" for d in dialogs]
    if integrity != "ok" or fk_problems:
        failures.append(f"database: integrity={integrity}, foreign-key problems={fk_problems}")
    if not closed:
        failures.append("the main window refused to close")
    if not pool_drained:
        failures.append("the Qt thread pool did not drain within 30 s")

    return {
        "tool": "tools/soak_test.py",
        "app_version": __version__,
        "started_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "python": platform.python_version(),
            "qt_platform": os.environ.get("QT_QPA_PLATFORM"),
            "sqlite": sqlite3.sqlite_version,
        },
        "parameters": {
            "minutes": args.minutes,
            "questions": args.questions,
            "seed": args.seed,
            "tracemalloc": bool(args.tracemalloc),
        },
        "duration_s": round(time.monotonic() - started, 1),
        "actions": {
            name: {
                "count": counts[name],
                "skipped": skipped[name],
                "returned_false": failed[name],
                "median_ms": round(statistics.median(durations[name]), 1) if durations[name] else None,
                "max_ms": round(max(durations[name]), 1) if durations[name] else None,
            }
            for name in names
        },
        # wall time per action, including any waiting for its background work (event loop running)
        "action_duration_ms": {
            "p50": percentile(all_durations, 50),
            "p95": percentile(all_durations, 95),
            "p99": percentile(all_durations, 99),
            "max": round(max(all_durations), 1) if all_durations else 0.0,
        },
        "heartbeat_gap_ms": {
            "p99": percentile(heartbeat_gaps, 99),
            "max": round(max(heartbeat_gaps), 1) if heartbeat_gaps else 0.0,
            "note": "GUI responsiveness: lateness of a 50 ms timer over the whole run, actions included",
        },
        "resources": {
            "rss_kb_first": rss[0] if rss else None,
            "rss_kb_after_warmup": rss[min(warm, len(rss) - 1)] if rss else None,
            "rss_kb_last": rss[-1] if rss else None,
            "rss_kb_max": max(rss) if rss else None,
            "rss_growth_after_warmup_kb": growth(rss),
            "open_handles_first": handles[0] if handles else None,
            "open_handles_last": handles[-1] if handles else None,
            "open_handles_growth_after_warmup": growth(handles),
            "os_threads_max": max(threads) if threads else None,
            "os_threads_growth_after_warmup": growth(threads),
        },
        "database": {
            "integrity_check": integrity,
            "foreign_key_problems": fk_problems,
            "status_counts": status_counts,
            "revisions": revisions,
            "drafts_left": drafts,
        },
        "shutdown": {"window_closed": closed, "thread_pool_drained": pool_drained},
        "failures": failures,
        "passed": not failures,
        "samples": samples,
        "workdir": str(workdir),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--minutes", type=float, default=10.0)
    parser.add_argument("--questions", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=9)
    parser.add_argument("--output", type=Path, default=Path("soak-report.json"))
    parser.add_argument("--workdir", help="keep the synthetic data folder here (default: a temporary one)")
    parser.add_argument("--tracemalloc", action="store_true", help="also trace Python allocations (slower)")
    args = parser.parse_args(argv)
    try:
        report = run(args)
    except Exception:  # noqa: BLE001 - the recording excepthook would otherwise swallow it
        sys.excepthook = sys.__excepthook__
        traceback.print_exc()
        return 2
    sys.excepthook = sys.__excepthook__
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    keys = ("duration_s", "action_duration_ms", "heartbeat_gap_ms", "resources", "database", "passed")
    summary = {k: report[k] for k in keys}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    for failure in report["failures"][:20]:
        print("FAILURE:", failure.splitlines()[0] if failure else failure)
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
