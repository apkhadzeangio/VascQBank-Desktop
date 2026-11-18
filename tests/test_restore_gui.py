"""Restoring a backup through the real window, dialog and entry point (temporary synthetic data only)."""

from __future__ import annotations

import json
import os
import shutil
import stat

import pytest

pytest.importorskip("PySide6")

from bank import approve, seed_counts, simulate_edit, snapshot  # noqa: E402
from test_restore_service import questions  # noqa: E402

from vascqbank_desktop.app import main as app_main  # noqa: E402
from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services import restore_service  # noqa: E402
from vascqbank_desktop.services.backup_service import create_manual_backup  # noqa: E402
from vascqbank_desktop.services.restore_service import RecoveryOutcome, journal_path  # noqa: E402
from vascqbank_desktop.services.seed_service import sha256_file  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.startup import Outcome, prepare_storage  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.restore_dialog import RestoreDialog  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_BACKUPS, PAGE_CATALOG  # noqa: E402

pytestmark = pytest.mark.gui
WAIT_MS = 20_000
REVIEWER = "რამაზ აფხაძე"


@pytest.fixture
def setup(app_paths: AppPaths, styled_qapp):
    seed_counts(app_paths, {"ALI": 12, "VENOUS": 8})
    ReviewerProfile(SettingsStore(app_paths.settings_file)).set_name(REVIEWER)
    approve(app_paths, "ALI-0001")
    backup = create_manual_backup(app_paths).path
    in_backup = questions(app_paths)
    approve(app_paths, "ALI-0002")
    approve(app_paths, "VENOUS-0003")
    simulate_edit(app_paths, "ALI-0004", stem="ასლის შემდეგ შეცვლილი ტექსტი")
    return app_paths, backup, in_backup, questions(app_paths)


def open_window(qtbot, paths: AppPaths) -> MainWindow:
    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=60)
    qtbot.addWidget(window)
    window.show()
    return window


def checked(qtbot, dialog: RestoreDialog) -> None:
    qtbot.waitUntil(lambda: not dialog.is_checking(), timeout=WAIT_MS)


def run_dialog(qtbot, dialog: RestoreDialog) -> None:
    """What the reviewer does: wait for the check, tick the confirmation, press restore."""
    qtbot.addWidget(dialog)
    dialog.show()
    checked(qtbot, dialog)
    assert dialog.summary is not None, dialog.check_label.text()
    assert not dialog.restore_button.isEnabled()  # never without the confirmation
    dialog.confirm_box.setChecked(True)
    assert dialog.restore_button.isEnabled()
    dialog.restore_button.click()


def test_the_dialog_shows_the_backup_and_needs_explicit_confirmation(qtbot, setup) -> None:
    paths, backup, in_backup, _later = setup
    window = open_window(qtbot, paths)
    dialog = window.create_restore_dialog(backup)
    qtbot.addWidget(dialog)
    dialog.show()
    checked(qtbot, dialog)
    assert dialog.selected_entry and dialog.selected_entry.path == backup
    assert dialog.check_label.text() == strings.RS_VERIFIED
    details = dialog.details_label.text()
    assert "20" in details and "დამტკიცებული 1" in details
    assert dialog.cancel_button.isDefault() and not dialog.restore_button.autoDefault()
    assert not dialog.restore_button.isEnabled()
    assert dialog.loss_label.isVisible() and dialog.other_db_label.isHidden()
    dialog.reject()
    assert questions(paths) != in_backup  # cancelling changes nothing


def test_restoring_from_the_backup_page_refreshes_the_whole_window(qtbot, setup) -> None:
    paths, backup, in_backup, _later = setup
    window = open_window(qtbot, paths)
    window.show_page(PAGE_CATALOG)
    assert window.catalog_view.go_to("ALI-0002")
    window.show_page(PAGE_BACKUPS)
    assert window.refresh_counts().total_with(ReviewStatus.APPROVED) == 3

    dialog = window.create_restore_dialog(backup)
    run_dialog(qtbot, dialog)
    qtbot.waitUntil(lambda: dialog.result_value is not None or dialog.error is not None, timeout=WAIT_MS)
    assert dialog.result_value is not None, dialog.error
    window.apply_restore(dialog.result_value)

    assert questions(paths) == in_backup
    counts = window.refresh_counts()
    assert counts.total == 20 and counts.total_with(ReviewStatus.APPROVED) == 1
    assert window.catalog_view.current_uid == "ALI-0002"  # the reviewer's place is kept
    assert window.catalog_view.detail_view.detail.status is ReviewStatus.PENDING_REVIEW  # restored data
    safety = dialog.result_value.safety_backup
    assert safety is not None and window.backup_view.selected_entry.path == safety
    assert backup.name in window.backup_view.status_label.text()
    assert backup.name in window.welcome_view._storage_values["restored"].text()
    window.wait_for_background_work()
    qtbot.waitUntil(lambda: window.index_status is not None, timeout=WAIT_MS)
    window.catalog_view.set_search_text("შეცვლილი")  # the old index of the replaced data is gone
    qtbot.waitUntil(lambda: not window.catalog_view.is_busy(), timeout=WAIT_MS)
    assert window.catalog_view.model.rowCount() == 0


def test_text_in_the_editor_is_saved_as_a_draft_before_the_restore(qtbot, setup) -> None:
    paths, backup, *_ = setup
    window = open_window(qtbot, paths)
    window.show_page(PAGE_CATALOG)
    assert window.catalog_view.go_to("VENOUS-0005") and window.catalog_view.start_editing()
    window.catalog_view.editing.editor.stem_edit.setPlainText("ტექსტი, რომელიც აღდგენამდე იწერებოდა")
    assert window.prepare_for_restore() is None
    assert not window.catalog_view.is_editing  # back in the read-only view
    # the text went into the working database as a draft, so the safety backup will hold it
    assert window.draft_service.count() == 1


def test_a_running_export_blocks_the_restore(qtbot, setup, monkeypatch) -> None:
    paths, backup, _in_backup, later = setup
    window = open_window(qtbot, paths)
    monkeypatch.setattr(window.export_view, "is_running", lambda: True)
    dialog = window.create_restore_dialog(backup)
    run_dialog(qtbot, dialog)
    assert dialog.error_label.text() == strings.RS_BLOCKED_EXPORT and not dialog.is_restoring()
    assert questions(paths) == later
    dialog.reject()
    monkeypatch.undo()  # closing the window must not ask about a (pretended) running export


def test_a_damaged_backup_cannot_be_chosen(qtbot, setup) -> None:
    paths, backup, _in_backup, later = setup
    damaged = backup.with_name(backup.name.replace("-manual", "-copy"))
    shutil.copy2(backup, damaged)
    os.chmod(damaged, stat.S_IRUSR | stat.S_IWUSR)
    manifest = json.loads(backup.with_name(backup.name + ".json").read_text(encoding="utf-8"))
    manifest["sha256"] = "0" * 64
    damaged.with_name(damaged.name + ".json").write_text(json.dumps(manifest), encoding="utf-8")
    window = open_window(qtbot, paths)
    dialog = window.create_restore_dialog(damaged)
    qtbot.addWidget(dialog)
    dialog.show()
    checked(qtbot, dialog)
    assert dialog.summary is None
    assert strings.RS_PROBLEMS["CHECKSUM_MISMATCH"] in dialog.check_label.text()
    dialog.confirm_box.setChecked(True)
    assert not dialog.restore_button.isEnabled() and not dialog.start_restore()
    assert questions(paths) == later
    dialog.reject()


def test_a_restore_in_progress_cannot_be_cancelled_or_closed(qtbot, setup, monkeypatch) -> None:
    paths, backup, in_backup, _later = setup
    window = open_window(qtbot, paths)
    import threading

    gate = threading.Event()

    def hold(stage: str) -> None:  # the worker waits after copying until the test lets it go on
        if stage == "temp-created":
            gate.wait(10)

    monkeypatch.setattr(restore_service, "_checkpoint", hold)
    dialog = window.create_restore_dialog(backup)
    run_dialog(qtbot, dialog)
    assert dialog.is_restoring() and not dialog.cancel_button.isEnabled()
    dialog.reject()
    dialog.close()
    assert dialog.isVisible() and dialog.error_label.text() == strings.RS_RUNNING
    gate.set()
    qtbot.waitUntil(lambda: dialog.result_value is not None, timeout=WAIT_MS)
    assert questions(paths) == in_backup


def test_a_refused_restore_reports_that_nothing_changed(qtbot, setup) -> None:
    paths, backup, _in_backup, later = setup
    window = open_window(qtbot, paths)
    dialog = window.create_restore_dialog(backup)
    other = restore_service.open_database(paths.database_file)  # another program has the database open
    try:
        run_dialog(qtbot, dialog)
        qtbot.waitUntil(lambda: dialog.error is not None, timeout=WAIT_MS)
    finally:
        other.close()
    assert strings.RS_PROBLEMS["DATABASE_BUSY"] in dialog.error_label.text()
    assert strings.RS_FAILED_SAFE in dialog.error_label.text()
    assert questions(paths) == later
    dialog.reject()


# ------------------------------------------------------------------ start-up
def test_a_missing_database_can_be_restored_from_the_recovery_message(qtbot, setup, monkeypatch) -> None:
    paths, backup, in_backup, _later = setup
    paths.database_file.unlink()
    answers = iter(["restore", "restore"])
    monkeypatch.setattr(app_main, "ask_recovery", lambda *args: next(answers))
    attempts: list[int] = []

    def restore_like_the_reviewer(p: AppPaths, _ms: int = 0) -> bool:
        attempts.append(1)
        if len(attempts) == 1:
            return False  # first time: the reviewer cancelled the dialog -> asked again
        restore_service.restore_backup(p, backup, expected_sha256=sha256_file(backup))
        return True

    monkeypatch.setattr(app_main, "ask_restore", restore_like_the_reviewer)
    result = app_main.startup(paths, None)
    assert not isinstance(result, int) and result.outcome is Outcome.READY
    assert len(attempts) == 2 and questions(paths) == in_backup


def test_the_restore_dialog_for_a_missing_database(qtbot, setup) -> None:
    paths, backup, in_backup, _later = setup
    paths.database_file.unlink()
    dialog = RestoreDialog(paths, current=None)
    assert dialog.confirm_box.text() == strings.RS_CONFIRM_CHECK_MISSING and dialog.loss_label.isHidden()
    run_dialog(qtbot, dialog)
    qtbot.waitUntil(lambda: dialog.result_value is not None, timeout=WAIT_MS)
    assert dialog.result_value.safety_backup is None and questions(paths) == in_backup


def test_an_ambiguous_interrupted_restore_stops_the_start(qtbot, setup) -> None:
    paths, *_ = setup
    journal_path(paths).parent.mkdir(parents=True, exist_ok=True)
    journal_path(paths).write_text("not a journal", encoding="utf-8")
    before = snapshot(paths)
    assert app_main.startup(paths, None, exit_after_ms=300) == app_main.EXIT_RESTORE_INTERRUPTED
    assert snapshot(paths) == before and journal_path(paths).exists()
    assert app_main.run_self_check(paths) == app_main.EXIT_RESTORE_INTERRUPTED


def test_a_finished_interrupted_restore_is_announced_once(qtbot, setup, monkeypatch) -> None:
    paths, backup, in_backup, _later = setup

    def crash(stage: str) -> None:
        if stage == "published":
            raise SystemExit("crash")

    monkeypatch.setattr(restore_service, "_checkpoint", crash)
    with pytest.raises(SystemExit):
        restore_service.restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    monkeypatch.undo()
    shown: list[object] = []
    monkeypatch.setattr(app_main, "show_restore_notice", lambda recovery, _ms=0: shown.append(recovery))
    result = app_main.startup(paths, None)
    assert not isinstance(result, int) and result.outcome is Outcome.READY
    assert [r.outcome for r in shown] == [RecoveryOutcome.COMPLETED] and questions(paths) == in_backup
    assert prepare_storage(paths, None).restore_recovery is None


def test_only_one_program_instance_per_data_folder(app_paths: AppPaths, styled_qapp) -> None:
    first = app_main.acquire_instance_lock(app_paths)
    assert first is not None
    try:
        assert app_main.acquire_instance_lock(app_paths) is None
    finally:
        first.unlock()
    again = app_main.acquire_instance_lock(app_paths)
    assert again is not None
    again.unlock()
