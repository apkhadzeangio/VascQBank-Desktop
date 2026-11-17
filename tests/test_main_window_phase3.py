"""Main window integration: subjects, statistics, reviewer, session persistence, backups, warm-up."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from bank import make_question, seed, seed_counts, set_status, snapshot  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from synthetic import question_block, write_pair  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.db.backup import BackupError  # noqa: E402
from vascqbank_desktop.domain.catalog import SearchMode  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services import backup_service  # noqa: E402
from vascqbank_desktop.services.settings_service import SavedSession, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings, window_state  # noqa: E402
from vascqbank_desktop.ui.main_window import (  # noqa: E402
    DEFAULT_HEIGHT,
    DEFAULT_WIDTH,
    MIN_WIDTH,
    MainWindow,
)
from vascqbank_desktop.ui.sidebar import PAGE_BACKUPS, PAGE_CATALOG, PAGE_HOME, PAGE_REVIEWER  # noqa: E402

pytestmark = pytest.mark.gui

WAIT_MS = 20_000


def open_window(qtbot, app_paths: AppPaths) -> MainWindow:
    window = MainWindow(initialize_storage(app_paths), SUBJECTS)
    qtbot.addWidget(window)
    window.show()
    return window


@pytest.fixture
def populated(app_paths: AppPaths, styled_qapp) -> AppPaths:
    seed_counts(app_paths, {"ALI": 30, "VENOUS": 20, "PAD_CLTI": 10})
    return app_paths


@pytest.fixture
def window(qtbot, populated: AppPaths) -> MainWindow:
    return open_window(qtbot, populated)


def settle(qtbot, window: MainWindow) -> None:
    qtbot.waitUntil(lambda: not window.catalog_view.is_busy(), timeout=WAIT_MS)


# ------------------------------------------------------------------ sidebar subjects


def test_only_subjects_with_questions_can_be_opened(window: MainWindow) -> None:
    enabled = {code for code, row in window.sidebar.subject_rows.items() if row.isEnabled()}
    assert enabled == {"ALI", "VENOUS", "PAD_CLTI"}
    assert window.sidebar.all_subjects_row.isEnabled()
    assert window.sidebar.subject_count_labels["ALI"].text() == "30 / 900"
    assert window.sidebar.subject_count_labels["MESENTERIC_RENAL"].text() == "0 / 600"
    assert window.sidebar.all_subjects_row.count_label.text() == "60 / 10 000"


def test_clicking_a_subject_opens_the_catalog_for_it(qtbot, window: MainWindow) -> None:
    assert window.pages.currentWidget() is window.welcome_view  # nothing remembered: starts at home
    row = window.sidebar.subject_rows["VENOUS"]
    QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    assert window.pages.currentWidget() is window.catalog_view
    assert window.catalog_view.current_filter.subject_code == "VENOUS"
    assert window.catalog_view.model.rowCount() == 20
    assert window.sidebar.selected_subject == "VENOUS" and row.is_selected
    assert not window.sidebar.subject_rows["ALI"].is_selected
    assert window.sidebar.nav_list.currentItem().text() == strings.NAV_CATALOG


def test_a_subject_without_questions_does_nothing_when_clicked(qtbot, window: MainWindow) -> None:
    QTest.mouseClick(window.sidebar.subject_rows["MESENTERIC_RENAL"], Qt.MouseButton.LeftButton)
    assert window.pages.currentWidget() is window.welcome_view
    assert window.catalog_view.current_filter.subject_code is None


def test_subject_rows_work_from_the_keyboard(qtbot, window: MainWindow) -> None:
    row = window.sidebar.subject_rows["PAD_CLTI"]
    row.setFocus()
    QTest.keyClick(row, Qt.Key.Key_Return)
    assert window.catalog_view.current_filter.subject_code == "PAD_CLTI"
    QTest.keyClick(window.sidebar.all_subjects_row, Qt.Key.Key_Space)
    assert window.catalog_view.current_filter.subject_code is None and window.sidebar.selected_subject is None


def test_the_sidebar_follows_the_subject_combo_box(qtbot, window: MainWindow) -> None:
    window.show_page(PAGE_CATALOG)
    window.catalog_view.subject_combo.setCurrentIndex(1 + [s.code for s in SUBJECTS].index("ALI"))
    assert window.sidebar.selected_subject == "ALI" and window.sidebar.subject_rows["ALI"].is_selected
    window.catalog_view.subject_combo.setCurrentIndex(0)
    assert window.sidebar.all_subjects_row.is_selected


def test_navigation_pages_are_all_reachable(qtbot, window: MainWindow) -> None:
    nav = window.sidebar.nav_list
    for row, widget in enumerate(
        (window.welcome_view, window.catalog_view, window.backup_view, window.reviewer_view)
    ):
        nav.setCurrentRow(row)
        assert window.pages.currentWidget() is widget


# ------------------------------------------------------------------ statistics


def test_statistics_come_from_the_database(qtbot, populated: AppPaths) -> None:
    for uid, status in (
        ("ALI-0001", ReviewStatus.APPROVED),
        ("ALI-0002", ReviewStatus.APPROVED),
        ("ALI-0003", ReviewStatus.IN_REVIEW),
        ("VENOUS-0001", ReviewStatus.REJECTED),
        ("PAD_CLTI-0001", ReviewStatus.APPROVED),
    ):
        set_status(populated, uid, status)
    window = open_window(qtbot, populated)
    panel = window.welcome_view.progress_panel
    bank = panel.bank_scope
    assert bank.total_label.text() == "60"
    assert {s: label.text() for s, label in bank.status_labels.items()} == {
        ReviewStatus.PENDING_REVIEW: "55",
        ReviewStatus.IN_REVIEW: "1",
        ReviewStatus.APPROVED: "3",
        ReviewStatus.REJECTED: "1",
    }
    assert panel.subject_scope.isHidden()  # no subject selected yet ...
    assert not panel.subject_hint.isHidden()  # ... so the panel says how to get subject figures

    window.open_subject("ALI")  # selected subject: its own figures
    scope = panel.subject_scope
    assert not scope.isHidden() and scope.title_label.text() == "კიდურის მწვავე იშემია"
    assert scope.total_label.text() == "30"
    assert {s: label.text() for s, label in scope.status_labels.items()} == {
        ReviewStatus.PENDING_REVIEW: "27",
        ReviewStatus.IN_REVIEW: "1",
        ReviewStatus.APPROVED: "2",
        ReviewStatus.REJECTED: "0",
    }
    assert scope.share_label.text() == strings.PROG_SHARE.format(percent="6.7%")
    assert [s for s, _ in scope.bar.segments()][0] is ReviewStatus.APPROVED
    strip = window.catalog_view.strip
    assert "<b>27</b>" in strip.subject_line.status_labels[ReviewStatus.PENDING_REVIEW].text()
    assert "<b>55</b>" in strip.bank_line.status_labels[ReviewStatus.PENDING_REVIEW].text()


def test_statistics_follow_later_changes(qtbot, window: MainWindow, populated: AppPaths) -> None:
    window.open_subject("VENOUS")
    set_status(populated, "VENOUS-0005", ReviewStatus.APPROVED)
    counts = window.refresh_counts()
    assert counts.total_with(ReviewStatus.APPROVED) == 1
    assert window.welcome_view.progress_panel.subject_scope.status_labels[ReviewStatus.APPROVED].text() == "1"
    assert window.bank_label.text() == strings.STATUS_BANK.format(total=60)


# ------------------------------------------------------------------ reviewer profile


def test_reviewer_name_is_saved_locally_and_shown(qtbot, window: MainWindow, populated: AppPaths) -> None:
    view = window.reviewer_view
    window.show_page(PAGE_REVIEWER)
    assert view.current_label.text() == strings.REV_NONE and not view.save_button.isEnabled()
    presets = [view.name_combo.itemText(i) for i in range(view.name_combo.count())]
    assert presets == ["რამაზი აფხაძე", "დავით ფარღალავა"]

    view.name_combo.setCurrentIndex(1)  # დავით ფარღალავა
    assert view.save_button.isEnabled()
    with qtbot.waitSignal(view.reviewerChanged, timeout=1000) as signal:
        view.save_button.click()
    assert signal.args == ["დავით ფარღალავა"]
    assert view.current_label.text() == "დავით ფარღალავა" and not view.save_button.isEnabled()
    assert window.reviewer_label.text() == strings.STATUS_REVIEWER.format(name="დავით ფარღალავა")
    assert (
        json.loads(populated.settings_file.read_text(encoding="utf-8"))["reviewer_name"] == "დავით ფარღალავა"
    )

    again = open_window(qtbot, populated)  # the next program start remembers it
    assert again.reviewer_view.current_label.text() == "დავით ფარღალავა"
    assert again.reviewer_label.text() == strings.STATUS_REVIEWER.format(name="დავით ფარღალავა")


def test_custom_names_are_allowed_and_offered_again(qtbot, window: MainWindow, populated: AppPaths) -> None:
    view = window.reviewer_view
    view.name_combo.setCurrentText("  Dr.  Nino  Beridze ")
    view.save()
    assert view.current_label.text() == "Dr. Nino Beridze"  # tidied
    reopened = open_window(qtbot, populated).reviewer_view
    items = [reopened.name_combo.itemText(i) for i in range(reopened.name_combo.count())]
    assert items == ["რამაზი აფხაძე", "დავით ფარღალავა", "Dr. Nino Beridze"]


def test_invalid_names_cannot_be_saved(window: MainWindow) -> None:
    view = window.reviewer_view
    view.name_combo.setCurrentText("")
    assert not view.save_button.isEnabled() and not view.message_label.isVisibleTo(
        view
    )  # just not saveable yet
    view.name_combo.setCurrentText("ა" * 100)
    assert not view.save_button.isEnabled() and view.message_label.isVisibleTo(view)
    assert view.message_label.text() == strings.REV_ERR_TOO_LONG.format(max=80)
    view.name_combo.setCurrentText("bad​name")
    assert view.message_label.text() == strings.REV_ERR_CHARACTERS and not view.save_button.isEnabled()
    view.save()  # even a forced call changes nothing
    assert view.saved_name == ""


def test_changing_the_name_never_touches_history(qtbot, window: MainWindow, populated: AppPaths) -> None:
    before = snapshot(populated)
    for name in ("რამაზი აფხაძე", "დავით ფარღალავა"):
        window.reviewer_view.name_combo.setCurrentText(name)
        window.reviewer_view.save()
    assert snapshot(populated) == before


def test_saving_the_name_reports_a_write_failure(qtbot, window: MainWindow, monkeypatch) -> None:
    def refuse(*_args, **_kwargs):
        raise PermissionError("settings folder is read-only")

    monkeypatch.setattr(window.settings_store, "save", refuse)
    window.reviewer_view.name_combo.setCurrentText("დავით ფარღალავა")
    window.reviewer_view.save()
    assert window.reviewer_view.message_label.text().startswith("სახელის შენახვა ვერ მოხერხდა")
    assert window.reviewer_view.saved_name == ""


# ------------------------------------------------------------------ session persistence


def saved(populated: AppPaths) -> SavedSession:
    return SettingsStore(populated.settings_file).load().session


def test_a_fresh_installation_starts_at_the_home_page(window: MainWindow) -> None:
    assert window.pages.currentWidget() is window.welcome_view
    assert window.catalog_view.current_uid is None
    assert not window.welcome_view.resume_card.isVisibleTo(window)


def test_the_working_context_is_restored_on_the_next_launch(qtbot, populated: AppPaths) -> None:
    set_status(populated, "VENOUS-0012", ReviewStatus.IN_REVIEW)
    first = open_window(qtbot, populated)
    first.open_subject("VENOUS")
    first.catalog_view.set_status(ReviewStatus.IN_REVIEW)
    first.catalog_view.go_to("VENOUS-0012")
    first.close()

    assert saved(populated) == SavedSession("VENOUS", ReviewStatus.IN_REVIEW, "VENOUS-0012")
    second = open_window(qtbot, populated)
    view = second.catalog_view
    assert second.pages.currentWidget() is view  # straight back to work
    assert (
        view.current_filter.subject_code == "VENOUS" and view.current_filter.status is ReviewStatus.IN_REVIEW
    )
    assert view.current_uid == "VENOUS-0012" and view.detail_view.uid_label.text() == "VENOUS-0012"
    assert view.list_view.currentIndex().row() == 0
    assert view.subject_combo.currentText() == "ვენური დაავადებები"
    assert view.status_combo.currentText() == ReviewStatus.IN_REVIEW.label_ka
    assert second.sidebar.selected_subject == "VENOUS" and not view.notice_label.isVisibleTo(view)
    assert second.welcome_view.resume_label.text() == strings.HOME_RESUME_BODY.format(uid="VENOUS-0012")


def test_the_session_is_saved_while_working_not_only_on_exit(
    qtbot, window: MainWindow, populated: AppPaths
) -> None:
    window.open_subject("ALI")
    window.catalog_view.go_to("ALI-0009")
    qtbot.waitUntil(lambda: saved(populated) == SavedSession("ALI", None, "ALI-0009"), timeout=5000)
    window.catalog_view.next_question()  # a crash after this point still remembers the place
    qtbot.waitUntil(lambda: saved(populated).question_uid == "ALI-0010", timeout=5000)


def test_rapid_navigation_is_coalesced_into_few_writes(qtbot, window: MainWindow, monkeypatch) -> None:
    writes: list[SavedSession] = []
    real = window.settings_store.set_session
    monkeypatch.setattr(window.settings_store, "set_session", lambda s: (writes.append(s), real(s))[1])
    window.open_subject("ALI")
    window.catalog_view.go_to("ALI-0001")
    for _ in range(15):  # holding the arrow key
        window.catalog_view.next_question()
    qtbot.waitUntil(lambda: bool(writes), timeout=5000)
    assert len(writes) == 1 and writes[0].question_uid == "ALI-0016"


def test_restoring_falls_back_when_the_question_no_longer_matches_the_filter(
    qtbot, populated: AppPaths
) -> None:
    for uid in ("ALI-0003", "ALI-0004", "ALI-0009"):
        set_status(populated, uid, ReviewStatus.IN_REVIEW)
    first = open_window(qtbot, populated)
    first.open_subject("ALI")
    first.catalog_view.set_status(ReviewStatus.IN_REVIEW)
    first.catalog_view.go_to("ALI-0004")
    first.close()
    set_status(populated, "ALI-0004", ReviewStatus.APPROVED)  # approved meanwhile (e.g. another session)

    second = open_window(qtbot, populated)
    view = second.catalog_view
    assert view.current_uid == "ALI-0009"  # the next question that is still in review
    assert view.current_filter.status is ReviewStatus.IN_REVIEW
    assert view.notice_label.isVisibleTo(view)
    assert view.notice_label.text() == strings.NOTICE_QUESTION_FILTERED.format(uid="ALI-0004")


def test_restoring_a_question_that_no_longer_exists(qtbot, populated: AppPaths) -> None:
    SettingsStore(populated.settings_file).set_session(SavedSession("VENOUS", None, "VENOUS-0999"))
    window = open_window(qtbot, populated)
    view = window.catalog_view
    assert view.current_uid == "VENOUS-0001" and view.current_filter.subject_code == "VENOUS"
    assert view.notice_label.text() == strings.NOTICE_QUESTION_MISSING.format(uid="VENOUS-0999")
    assert window.pages.currentWidget() is view


def test_restoring_never_changes_a_review_status(qtbot, populated: AppPaths) -> None:
    set_status(populated, "ALI-0002", ReviewStatus.IN_REVIEW)
    SettingsStore(populated.settings_file).set_session(
        SavedSession("ALI", ReviewStatus.IN_REVIEW, "ALI-0002")
    )
    before = snapshot(populated)
    window = open_window(qtbot, populated)
    window.catalog_view.next_question()
    window.catalog_view.previous_question()
    window.close()
    assert snapshot(populated) == before
    assert sqlite3.connect(populated.database_file).execute(
        "SELECT status FROM questions WHERE question_uid = 'ALI-0002'"
    ).fetchone() == ("IN_REVIEW",)


def test_a_corrupt_settings_file_does_not_stop_the_program(qtbot, populated: AppPaths) -> None:
    populated.settings_file.write_bytes(b"{ this is not json \xff\xfe")
    window = open_window(qtbot, populated)
    assert window.pages.currentWidget() is window.welcome_view
    assert window.reviewer_view.current_label.text() == strings.REV_NONE
    assert list(populated.data_dir.glob("settings.json.corrupt-*"))  # kept, not silently discarded
    window.reviewer_view.name_combo.setCurrentText("ა ბ")
    window.reviewer_view.save()  # and the program can write settings again
    assert json.loads(populated.settings_file.read_text(encoding="utf-8"))["reviewer_name"] == "ა ბ"


def test_an_unwritable_settings_file_does_not_break_navigation(
    qtbot, window: MainWindow, monkeypatch
) -> None:
    def refuse(_session):
        raise OSError("disk full")

    monkeypatch.setattr(window.settings_store, "set_session", refuse)
    window.open_subject("ALI")
    window.catalog_view.go_to("ALI-0002")
    window._flush_session()  # must log and carry on, not raise
    assert window.catalog_view.current_uid == "ALI-0002"


def test_settings_stay_out_of_the_database_and_the_repository(populated: AppPaths) -> None:
    assert populated.settings_file.parent == populated.data_dir
    repo_root = Path(__file__).resolve().parent.parent
    assert repo_root not in populated.settings_file.parents  # tests never write into the source tree
    assert "settings.json" in (repo_root / ".gitignore").read_text(encoding="utf-8")


# ------------------------------------------------------------------ window geometry


def test_window_geometry_is_saved_and_restored(qtbot, populated: AppPaths) -> None:
    first = open_window(qtbot, populated)
    first.resize(1100, 700)
    first.close()
    geometry = SettingsStore(populated.settings_file).load().window_geometry
    assert geometry and geometry.isascii()
    second = open_window(qtbot, populated)
    assert second.width() >= MIN_WIDTH  # restored geometry never undercuts the minimum size


def test_an_unreachable_saved_geometry_falls_back_to_the_default(
    qtbot, populated: AppPaths, monkeypatch
) -> None:
    first = open_window(qtbot, populated)
    first.close()
    monkeypatch.setattr(window_state, "is_reachable", lambda *_args: False)  # e.g. the second monitor is gone
    second = open_window(qtbot, populated)
    assert (second.width(), second.height()) == (DEFAULT_WIDTH, DEFAULT_HEIGHT)


def test_garbage_geometry_in_the_settings_is_ignored(qtbot, populated: AppPaths) -> None:
    SettingsStore(populated.settings_file).set_window_geometry("QUJDREVGRw==")
    window = open_window(qtbot, populated)
    assert (window.width(), window.height()) == (DEFAULT_WIDTH, DEFAULT_HEIGHT)


# ------------------------------------------------------------------ backups page


def test_manual_backup_from_the_page(qtbot, window: MainWindow, populated: AppPaths) -> None:
    view = window.backup_view
    window.show_page(PAGE_BACKUPS)
    assert view.table.rowCount() == 0 and view.empty_label.isVisibleTo(view)
    assert not view.verify_button.isEnabled()  # nothing to verify yet
    before = snapshot(populated)

    with qtbot.waitSignal(view.backupCreated, timeout=WAIT_MS) as created:
        view.create_button.click()
        assert not view.create_button.isEnabled()  # busy: no second click can start another
    assert not view.is_busy()
    entry = created.args[0]
    assert entry.path.is_file() and entry.path.parent == populated.backups_dir
    assert view.table.rowCount() == 1 and not view.empty_label.isVisibleTo(view)
    assert view.table.item(0, 1).text() == strings.BK_KIND_MANUAL
    assert view.table.item(0, 2).text() == "60"  # questions inside the backup
    assert view.table.item(0, 4).text() == strings.BK_STATUS_VERIFIED
    assert view.table.item(0, 5).text() == entry.name
    assert view.table.item(0, 0).text() == window_time(entry.created_at)
    assert view.status_label.text() == strings.BK_CREATED.format(name=entry.name)
    assert view.summary_label.text() == strings.BK_SUMMARY.format(count=1)
    assert snapshot(populated) == before  # the live database was only read


def window_time(iso: str) -> str:
    from vascqbank_desktop.ui.formatting import format_timestamp

    return format_timestamp(iso)


def test_backup_list_shows_time_kind_size_and_verification(
    qtbot, window: MainWindow, populated: AppPaths
) -> None:
    from vascqbank_desktop.db.backup import create_backup
    from vascqbank_desktop.db.connection import open_database

    conn = open_database(populated.database_file)
    try:
        create_backup(conn, populated.backups_dir, "pre-import")
        create_backup(conn, populated.backups_dir, "manual")
    finally:
        conn.close()
    view = window.backup_view
    window.show_page(PAGE_BACKUPS)  # opening the page lists them and verifies in the background
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    assert view.table.rowCount() == 2
    kinds = {view.table.item(r, 1).text() for r in range(2)}
    assert kinds == {strings.BK_KIND_MANUAL, strings.BK_KIND_IMPORT}
    for row in range(2):
        assert view.table.item(row, 4).text() == strings.BK_STATUS_VERIFIED
        assert view.table.item(row, 3).text().endswith(("KB", "MB"))
        assert len(view.table.item(row, 0).text()) == len("2026-11-17 21:20")
    assert strings.BK_VERIFY_DONE.format(ok=2, bad=0) == view.status_label.text()


def test_a_damaged_backup_is_reported_not_trusted(qtbot, window: MainWindow, populated: AppPaths) -> None:
    created = backup_service.create_manual_backup(populated)
    with created.path.open("r+b") as handle:
        handle.seek(created.size // 2)
        byte = handle.read(1)
        handle.seek(created.size // 2)
        handle.write(bytes([byte[0] ^ 0xFF]))
    view = window.backup_view
    window.show_page(PAGE_BACKUPS)
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    status = view.table.item(0, 4)
    assert status.text().startswith(strings.BK_STATUS_FAILED)
    assert strings.BK_PROBLEM_CHECKSUM_MISMATCH in status.text()
    assert view.status_label.objectName() == "ErrorNotice"
    assert view.status_label.text() == strings.BK_VERIFY_DONE.format(ok=0, bad=1)


def test_verify_all_rechecks_everything(qtbot, window: MainWindow, populated: AppPaths) -> None:
    backup_service.create_manual_backup(populated)
    view = window.backup_view
    window.show_page(PAGE_BACKUPS)
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    view.verify_button.click()
    assert not view.verify_button.isEnabled()
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    assert view.table.item(0, 4).text() == strings.BK_STATUS_VERIFIED


def test_a_failed_backup_is_reported_and_changes_nothing(
    qtbot, window: MainWindow, populated: AppPaths, monkeypatch
) -> None:
    def fail(_paths):
        raise BackupError("simulated: the backup failed verification")

    monkeypatch.setattr(backup_service, "create_manual_backup", fail)
    before = snapshot(populated)
    view = window.backup_view
    view.create_button.click()
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    assert view.status_label.objectName() == "ErrorNotice"
    assert "simulated" in view.status_label.text() and view.status_label.text().startswith(
        strings.BK_CREATE_FAILED[:20]
    )
    assert view.table.rowCount() == 0 and view.create_button.isEnabled()  # can simply try again
    assert snapshot(populated) == before


def test_there_is_no_restore_button_only_an_honest_note(window: MainWindow) -> None:
    from PySide6.QtWidgets import QAbstractButton

    view = window.backup_view
    window.show_page(PAGE_BACKUPS)
    labels = {b.text() for b in view.findChildren(QAbstractButton) if b.isVisibleTo(view) and b.text()}
    assert labels == {strings.BK_CREATE, strings.BK_VERIFY_ALL, strings.BK_OPEN_FOLDER}
    assert not any("აღდგენა" in label for label in labels)  # "restore" is not offered as an action
    assert view.restore_note.text() == strings.BK_RESTORE_NOTE


# ------------------------------------------------------------------ search index warm-up


def test_the_index_is_built_in_the_background_after_start(qtbot, app_paths: AppPaths, styled_qapp) -> None:
    seed(app_paths, [make_question("ALI", n) for n in range(1, 401)])  # too many to index inside a search
    window = open_window(qtbot, app_paths)
    window.wait_for_background_work()
    qtbot.waitUntil(lambda: window.index_status is not None, timeout=WAIT_MS)
    assert (
        window.index_status.available and window.index_status.rebuilt and window.index_status.indexed == 400
    )
    assert app_paths.search_index_file.is_file()
    window.catalog_view.set_search_text("სატესტო")
    settle(qtbot, window)
    assert window.catalog_view.model.results.mode is SearchMode.FTS


def test_search_works_before_the_index_exists(qtbot, app_paths: AppPaths, styled_qapp) -> None:
    seed(app_paths, [make_question("ALI", n) for n in range(1, 401)])
    window = open_window(qtbot, app_paths)
    window.catalog_view.set_search_text("სატესტო")  # typed immediately after start
    settle(qtbot, window)
    assert window.catalog_view.model.rowCount() == 400  # answered (by LIKE or by the finished index)


# ------------------------------------------------------------------ menus and shortcuts


def test_menu_actions_move_focus_and_navigate(qtbot, window: MainWindow) -> None:
    assert window.action_search.shortcut().toString() == "Ctrl+F"
    assert window.action_goto.shortcut().toString() == "Ctrl+G"
    assert window.action_previous.shortcut().toString() == "Alt+Left"
    assert window.action_next.shortcut().toString() == "Alt+Right"
    window.action_goto.trigger()
    assert window.pages.currentWidget() is window.catalog_view
    assert window.catalog_view.goto_edit.hasFocus()
    window.action_search.trigger()
    assert window.catalog_view.search_edit.hasFocus()
    window.catalog_view.go_to("ALI-0005")
    window.action_next.trigger()
    assert window.catalog_view.current_uid == "ALI-0006"
    window.action_previous.trigger()
    window.action_previous.trigger()
    assert window.catalog_view.current_uid == "ALI-0004"


# ------------------------------------------------------------------ import refreshes the catalog


def test_importing_questions_updates_the_catalog_and_the_sidebar(
    qtbot, app_paths: AppPaths, styled_qapp, tmp_path: Path
) -> None:
    source = tmp_path / "წყარო"
    write_pair(source, "ALI", range(1, 4))
    bad = question_block("VENOUS", 1).replace("*", "")
    write_pair(source, "VENOUS", [1], questions_text=bad + "\n")
    window = open_window(qtbot, app_paths)
    assert (
        window.catalog_view.model.rowCount() == 0
        and window.catalog_view.import_button.isVisibleTo(window) is False
    )

    dialog = window.create_import_dialog()
    qtbot.addWidget(dialog)
    dialog.add_paths([source])
    with qtbot.waitSignal(dialog.validationFinished, timeout=WAIT_MS):
        dialog.run_validation()
    dialog._confirm = lambda count: True  # type: ignore[method-assign]
    with qtbot.waitSignal(dialog.importFinished, timeout=WAIT_MS):
        dialog.confirm_import()
    qtbot.waitUntil(lambda: window.catalog_view.model.rowCount() == 3, timeout=WAIT_MS)
    assert (
        window.sidebar.subject_rows["ALI"].isEnabled()
        and not window.sidebar.subject_rows["VENOUS"].isEnabled()
    )
    window.open_subject("ALI")
    assert window.catalog_view.model.rowCount() == 3
    window.wait_for_background_work()
    assert window.welcome_view.progress_panel.bank_scope.total_label.text() == "3"


def test_closing_while_work_is_running_is_clean(qtbot, populated: AppPaths) -> None:
    window = open_window(qtbot, populated)
    window.catalog_view.set_search_text("ვარიანტი")
    window.catalog_view._commit_search()
    window.close()  # waits for the running search/warm-up instead of destroying a live thread
    assert not window.isVisible()
    assert window.catalog_view._runner.is_busy() is False


def test_the_two_named_reviewers_are_the_only_presets(window: MainWindow) -> None:
    from vascqbank_desktop.domain.reviewer import REVIEWER_PRESETS

    assert REVIEWER_PRESETS == ("რამაზი აფხაძე", "დავით ფარღალავა")
    assert PAGE_HOME and PAGE_BACKUPS  # page keys are exported for navigation
