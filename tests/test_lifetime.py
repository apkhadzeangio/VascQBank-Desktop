"""Phase 9b, campaign F — concurrency and object lifetime in the real main window.

* the window is closed while each background job is still running (search, index warm-up, autosave,
  manual / automatic backup, verification, save-copy, add-external, export preview): no exception,
  no Qt warning about deleted objects or running threads, the thread pool drains, no text is lost,
  no temporary file is left and the database is sound;
* a restore asked for while an automatic backup runs is refused until it has finished;
* another process holding a read transaction: reviewing goes on (WAL), a restore is refused safely
  and works once the reader is gone;
* 200 × approve-and-next fired as fast as key events can be queued; quick-filter switching while a
  search runs; resizing during work.

Synthetic data in temporary folders; offscreen Qt.
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import textwrap
import time

import pytest

pytest.importorskip("PySide6")

from bank import seed_counts  # noqa: E402
from PySide6.QtCore import Qt, QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services import (  # noqa: E402
    auto_backup,
    backup_portability,
    backup_service,
    catalog_service,
    restore_service,
)
from vascqbank_desktop.services.backup_service import create_manual_backup  # noqa: E402
from vascqbank_desktop.services.restore_service import RestoreProblem, RestoreRefused  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_BACKUPS, PAGE_CATALOG, PAGE_EXPORT  # noqa: E402

pytestmark = pytest.mark.gui
WAIT_MS = 30_000
REVIEWER = "სიცოცხლის ციკლის რეცენზენტი"
BAD_QT_MESSAGES = re.compile(
    r"Signal source has been deleted|Internal C\+\+ object|QThread: Destroyed|already deleted"
    r"|QObject::killTimer|QBasicTimer|Timers cannot|QSocketNotifier|wrapped C/C\+\+ object",
    re.I,
)
TEMP = re.compile(r"(\.partial$|\.tmp$|^\.restore-|^\.seed-install-|^\.vqb-export-|^\.vascqbank-)")


@pytest.fixture
def paths(app_paths: AppPaths, styled_qapp) -> AppPaths:
    seed_counts(app_paths, {"ALI": 60, "VENOUS": 30, "PAD_CLTI": 20})
    ReviewerProfile(SettingsStore(app_paths.settings_file)).set_name(REVIEWER)
    create_manual_backup(app_paths)
    return app_paths


def _window(qtbot, paths: AppPaths) -> MainWindow:
    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=150)
    qtbot.addWidget(window)
    window.resize(1280, 720)
    window.show()
    return window


def _sound(paths: AppPaths) -> None:
    conn = sqlite3.connect(paths.database_file)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()
    leftovers = [p.name for p in paths.data_dir.rglob("*") if TEMP.search(p.name)]
    assert not leftovers, leftovers


def _no_bad_qt_messages(qtlog) -> None:
    bad = [m.message for m in qtlog.records if BAD_QT_MESSAGES.search(m.message)]
    assert not bad, bad


def _slow(monkeypatch, module, name: str, seconds: float = 0.6) -> None:
    original = getattr(module, name)

    def slow(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        time.sleep(seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(module, name, slow)


def _start_search(window: MainWindow) -> None:
    window.show_page(PAGE_CATALOG)
    window.catalog_view.set_search_text("ვარიანტი")
    window.catalog_view._commit_search()


def _start_backup(window: MainWindow) -> None:
    window.show_page(PAGE_BACKUPS)
    window.backup_view.create_backup()


def _start_verify(window: MainWindow) -> None:
    window.show_page(PAGE_BACKUPS)
    window.backup_view.verify_all(force=True)


def _start_automatic(window: MainWindow) -> None:
    assert window.backup_view.start_automatic_backup()


def _start_save_copy(window: MainWindow) -> None:
    window.show_page(PAGE_BACKUPS)
    view = window.backup_view
    view.table.selectRow(0)
    target = window._paths.data_dir.parent / "usb"
    target.mkdir(exist_ok=True)
    assert view.save_copy(target)


def _start_add_external(window: MainWindow) -> None:
    window.show_page(PAGE_BACKUPS)
    other = AppPaths(data_dir=window._paths.data_dir.parent / "other")
    seed_counts(other, {"VENOUS": 2})
    assert window.backup_view.add_external(create_manual_backup(other).path)


def _start_preview(window: MainWindow) -> None:
    window.show_page(PAGE_EXPORT)
    window.export_view.request_preview()


def _start_warmup(window: MainWindow) -> None:
    window._start_index_warmup()


def _start_autosave(window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0007") and view.start_editing()
    view.editing.editor.stem_edit.setPlainText("დახურვამდე აკრეფილი ტექსტი — ხელოვნური")


JOBS = {
    # name: (start the job, (module, function) slowed down so the close happens while it runs)
    "search": (_start_search, (catalog_service.CatalogService, "query")),
    "index-warm-up": (_start_warmup, (catalog_service.CatalogService, "ensure_search_index")),
    "manual-backup": (_start_backup, (backup_service, "create_manual_backup")),
    "verify-all": (_start_verify, (backup_service, "verify_entry")),
    "automatic-backup": (_start_automatic, (auto_backup, "run_automatic_backup")),
    "save-copy": (_start_save_copy, (backup_portability, "save_backup_copy")),
    "add-external": (_start_add_external, (backup_portability, "import_external_backup")),
    "export-preview": (_start_preview, None),
    "autosave": (_start_autosave, None),
}


@pytest.mark.parametrize("job", sorted(JOBS))
def test_closing_the_window_while_a_job_runs_is_clean(
    qtbot, qtlog, paths: AppPaths, monkeypatch, job: str
) -> None:
    start, slowed = JOBS[job]
    if slowed is not None:
        _slow(monkeypatch, *slowed)
    window = _window(qtbot, paths)
    start(window)
    QApplication.processEvents()
    window.close()
    assert not window.isVisible()
    assert QThreadPool.globalInstance().waitForDone(WAIT_MS)
    QApplication.processEvents()
    _no_bad_qt_messages(qtlog)
    _sound(paths)
    if job == "autosave":  # the typed text was written as a draft before the window went away
        conn = sqlite3.connect(paths.database_file)
        try:
            stem = conn.execute(
                "SELECT d.stem FROM drafts d JOIN questions q ON q.id = d.question_id"
                " WHERE q.question_uid = 'ALI-0007'"
            ).fetchone()
        finally:
            conn.close()
        assert stem == ("დახურვამდე აკრეფილი ტექსტი — ხელოვნური",)
    # the next start opens the same data normally
    again = _window(qtbot, paths)
    again.close()


def test_a_restore_waits_for_a_running_automatic_backup(qtbot, paths: AppPaths, monkeypatch) -> None:
    _slow(monkeypatch, auto_backup, "run_automatic_backup", 1.0)
    window = _window(qtbot, paths)
    assert window.backup_view.start_automatic_backup()
    assert window.prepare_for_restore() == strings.RS_BLOCKED_BACKUP
    qtbot.waitUntil(lambda: not window.backup_view.is_creating(), timeout=WAIT_MS)
    assert window.prepare_for_restore() is None
    window.close()


READER = textwrap.dedent(
    """
    import sqlite3, sys, time
    conn = sqlite3.connect(sys.argv[1], isolation_level=None)
    conn.execute("BEGIN")
    conn.execute("SELECT count(*) FROM questions").fetchone()  # holds a read snapshot
    print("READING", flush=True)
    sys.stdin.readline()
    conn.execute("COMMIT")
    conn.close()
    """
)


def test_a_reader_in_another_process_blocks_only_the_restore(qtbot, paths: AppPaths) -> None:
    backup = backup_service.list_backup_entries(paths)[0].path
    reader = subprocess.Popen(
        [sys.executable, "-c", READER, str(paths.database_file)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )  # fmt: skip
    try:
        assert reader.stdout is not None and reader.stdout.readline().strip() == "READING"
        window = _window(qtbot, paths)
        view = window.catalog_view
        assert view.go_to("ALI-0001") and view.workflow.approve()  # WAL: writing goes on
        window.close()
        before = (paths.database_file.read_bytes(), sorted(p.name for p in paths.backups_dir.iterdir()))
        with pytest.raises(RestoreRefused) as caught:
            restore_service.restore_backup(
                paths, backup, expected_sha256=restore_service.inspect_backup(backup).sha256
            )
        assert caught.value.problem is RestoreProblem.DATABASE_BUSY
        assert paths.database_file.read_bytes() == before[0]
        assert not restore_service.journal_path(paths).exists()
    finally:
        assert reader.stdin is not None
        reader.stdin.write("\n")
        reader.stdin.flush()
        reader.wait(timeout=30)
    restore_service.restore_backup(
        paths, backup, expected_sha256=restore_service.inspect_backup(backup).sha256
    )
    _sound(paths)


def test_two_hundred_approve_and_next_in_a_burst(qtbot, qtlog, paths: AppPaths) -> None:
    window = _window(qtbot, paths)
    view = window.catalog_view
    window.show_page(PAGE_CATALOG)
    view.set_quick_filter(1)  # pending only
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    first = window.catalog_service.locate_id(view.model.results.ids[0]).uid  # the list's own order
    assert view.go_to(first)
    window.activateWindow()
    qtbot.waitUntil(window.isActiveWindow, timeout=WAIT_MS)  # shortcuts are window-scoped, as in use
    view.list_view.setFocus()
    for _ in range(200):  # one key event after the other, without waiting for the list to settle
        qtbot.keyClick(view.list_view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    QApplication.processEvents()
    window.close()
    _no_bad_qt_messages(qtlog)
    _sound(paths)
    conn = sqlite3.connect(paths.database_file)
    try:
        approved = conn.execute(
            "SELECT q.approved_revision_id = q.current_revision_id, "
            "(SELECT revision_id FROM review_events e WHERE e.question_id = q.id AND e.action = 'APPROVE' "
            " ORDER BY e.id DESC LIMIT 1) = q.approved_revision_id"
            " FROM questions q WHERE q.status = 'APPROVED'"
        ).fetchall()
        events = conn.execute("SELECT count(*) FROM review_events WHERE action = 'APPROVE'").fetchone()[0]
    finally:
        conn.close()
    assert len(approved) == 110  # every pending question once (the last 90 presses meet the list's end) …
    assert all(a == (1, 1) for a in approved)  # … each bound to its exact revision
    assert events == len(approved)  # and none twice


def test_switching_filters_while_a_search_runs(qtbot, qtlog, paths: AppPaths, monkeypatch) -> None:
    _slow(monkeypatch, catalog_service.CatalogService, "query", 0.2)
    window = _window(qtbot, paths)
    view = window.catalog_view
    window.show_page(PAGE_CATALOG)
    view.set_search_text("ALI")
    view._commit_search()
    for index in (2, 0, 3, 1, 5, 4, 0, 1):
        view.set_quick_filter(index)
        QApplication.processEvents()
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    listed = {window.catalog_service.locate_id(i).uid for i in view.model.results.ids}
    assert listed and all(uid.startswith("ALI") for uid in listed)  # search ∧ "pending" filter, nothing stale
    window.close()
    _no_bad_qt_messages(qtlog)


def test_resizing_while_working(qtbot, qtlog, paths: AppPaths) -> None:
    window = _window(qtbot, paths)
    view = window.catalog_view
    _start_search(window)
    window.backup_view.create_backup()
    for size in [(1280, 720), (900, 600), (1920, 1080), (1366, 768), (1280, 720)] * 4:
        window.resize(*size)
        QApplication.processEvents()
    assert view.go_to("ALI-0002")
    qtbot.waitUntil(lambda: not window.backup_view.is_busy() and not view.is_busy(), timeout=WAIT_MS)
    window.close()
    _no_bad_qt_messages(qtlog)
    _sound(paths)
