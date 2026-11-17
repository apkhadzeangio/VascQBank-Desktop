"""Main window and editing: menu actions, closing, restart recovery, status bar, page changes."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from bank import draft_row, revisions_of, seed_counts  # noqa: E402
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.edit_errors import StorageFailure, StorageProblem  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.question_editor_view import CancelChoice, EditorState  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_BACKUPS, PAGE_CATALOG, PAGE_HOME, PAGE_REVIEWER  # noqa: E402

pytestmark = pytest.mark.gui

WAIT_MS = 10_000
REVIEWER = "რამაზ აფხაძე"
UID = "ALI-0003"


def open_window(qtbot, paths: AppPaths) -> MainWindow:
    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=60)
    qtbot.addWidget(window)
    window.show()
    return window


def choose_reviewer(paths: AppPaths, name: str = REVIEWER) -> None:
    ReviewerProfile(SettingsStore(paths.settings_file)).set_name(name)


@pytest.fixture
def populated(app_paths: AppPaths, styled_qapp) -> AppPaths:
    seed_counts(app_paths, {"ALI": 30, "VENOUS": 20})
    choose_reviewer(app_paths)
    return app_paths


@pytest.fixture
def window(qtbot, populated: AppPaths) -> MainWindow:
    window = open_window(qtbot, populated)
    window.show_page(PAGE_CATALOG)
    return window


def edit(window: MainWindow, uid: str = UID) -> None:
    assert window.catalog_view.go_to(uid)
    assert window.catalog_view.start_editing()


def wait_state(qtbot, window: MainWindow, state: EditorState) -> None:
    editor = window.catalog_view.editing.editor
    qtbot.waitUntil(lambda: editor.state is state, timeout=WAIT_MS)


def type_text(widget, text: str) -> None:
    for char in text:
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(
                widget, QKeyEvent(kind, Qt.Key.Key_unknown, Qt.KeyboardModifier.NoModifier, char)
            )


# ------------------------------------------------------------------ menus and shortcuts


def test_the_menu_actions_open_the_editor_and_the_history(window: MainWindow) -> None:
    window.show_page(PAGE_HOME)
    assert window.catalog_view.go_to(UID)
    window.show_page(PAGE_HOME)
    window.action_edit.trigger()
    assert window.pages.currentWidget() is window.catalog_view  # the action brings the page forward
    assert window.catalog_view.is_editing
    assert window.catalog_view.editing.editor.cancel()
    window.action_history.trigger()
    assert window.catalog_view.editing.mode.value == "HISTORY"


def test_ctrl_e_opens_the_editor_and_ctrl_s_saves(qtbot, window: MainWindow) -> None:
    assert window.catalog_view.go_to(UID)
    window.catalog_view.detail_view.setFocus()
    qtbot.keyClick(window.catalog_view, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    assert window.catalog_view.is_editing
    editor = window.catalog_view.editing.editor
    editor.stem_edit.setPlainText("შენახული კლავიატურით")
    qtbot.keyClick(editor.stem_edit, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert [r["stem"] for r in revisions_of(window.catalog_view.service._paths, UID)][-1] == (
        "შენახული კლავიატურით"
    )


def test_without_a_reviewer_the_edit_action_leads_to_the_reviewer_page(
    qtbot, app_paths: AppPaths, styled_qapp
) -> None:
    seed_counts(app_paths, {"ALI": 5})
    window = open_window(qtbot, app_paths)
    window.show_page(PAGE_CATALOG)
    assert window.catalog_view.go_to("ALI-0002")
    window.action_edit.trigger()
    assert not window.catalog_view.is_editing
    assert window.pages.currentWidget() is window.reviewer_view
    assert window.reviewer_label.text() == strings.STATUS_REVIEWER_NONE


# ------------------------------------------------------------------ status, counts


def test_saving_the_first_edit_moves_the_question_to_in_review_in_the_counters(
    qtbot, window: MainWindow
) -> None:
    edit(window)
    window.catalog_view.editing.editor.stem_edit.setPlainText("ახალი ტექსტი")
    assert window.catalog_view.editing.editor.save()
    strip = window.catalog_view.strip
    assert "<b>1</b>" in strip.bank_line.status_labels[ReviewStatus.IN_REVIEW].text()
    assert "<b>49</b>" in strip.bank_line.status_labels[ReviewStatus.PENDING_REVIEW].text()


def test_drafts_show_in_the_status_bar_and_on_the_home_page(qtbot, window: MainWindow) -> None:
    assert window.drafts_label.isHidden() and window.welcome_view.drafts_card.isHidden()
    edit(window)
    editor = window.catalog_view.editing.editor
    editor.stem_edit.setPlainText("დაუსრულებელი ტექსტი")
    wait_state(qtbot, window, EditorState.DRAFT_SAVED)
    qtbot.waitUntil(lambda: not window.drafts_label.isHidden(), timeout=WAIT_MS)
    assert window.drafts_label.text() == strings.STATUS_DRAFTS.format(count=1)
    window.show_page(PAGE_HOME)
    assert not window.welcome_view.drafts_card.isHidden()
    assert window.welcome_view.drafts_label.text() == strings.DR_BANNER.format(count=1)
    window.welcome_view.drafts_button.click()  # opens the list of drafts over the catalog
    assert window.pages.currentWidget() is window.catalog_view
    dialog = window.catalog_view.editing._dialogs[-1]
    assert [row.uid for row in dialog.rows] == [UID]
    dialog.close()


def test_leaving_the_catalog_page_persists_a_pending_draft(window: MainWindow) -> None:
    edit(window)
    window.catalog_view.editing.editor.stem_edit.setPlainText("ტექსტი გვერდის დატოვებისას")
    assert draft_row(window._paths, UID) is None  # not yet: the pause has not elapsed
    window.show_page(PAGE_BACKUPS)
    assert draft_row(window._paths, UID)["stem"] == "ტექსტი გვერდის დატოვებისას"  # type: ignore[index]
    window.show_page(PAGE_REVIEWER)
    assert window.catalog_view.is_editing  # and the editor is exactly as it was
    window.show_page(PAGE_CATALOG)
    assert window.catalog_view.editing.editor.stem_edit.toPlainText() == "ტექსტი გვერდის დატოვებისას"


# ------------------------------------------------------------------ closing and restarting


def test_closing_the_window_saves_the_draft_and_the_next_start_offers_it(qtbot, populated: AppPaths) -> None:
    first = open_window(qtbot, populated)
    first.show_page(PAGE_CATALOG)
    edit(first)
    editor = first.catalog_view.editing.editor
    type_text(editor.stem_edit, " — დაუსრულებელი სიტყვა")
    assert editor.autosaver.pending  # the pause has not elapsed
    assert first.close()
    saved = draft_row(populated, UID)
    assert saved is not None and saved["stem"].startswith(" — დაუსრულებელი სიტყვა")
    assert len(revisions_of(populated, UID)) == 1  # a draft, not a revision

    second = open_window(qtbot, populated)  # the program is started again
    view = second.catalog_view
    assert second.pages.currentWidget() is view  # back where the reviewer stopped…
    assert view.current_uid == UID
    assert not view.is_editing  # …but nothing is restored behind their back
    assert "დაუსრულებელი სიტყვა" not in view.detail_view.stem_label.text()
    assert view.editing.draft_bar.isVisible()
    assert second.drafts_label.text() == strings.STATUS_DRAFTS.format(count=1)
    assert second.reviewer_label.text() == strings.STATUS_REVIEWER.format(name=REVIEWER)
    view.editing.draft_bar.restore_button.click()
    assert view.is_editing
    assert view.editing.editor.stem_edit.toPlainText().startswith(" — დაუსრულებელი სიტყვა")
    assert view.editing.editor.state is EditorState.DRAFT_SAVED


def test_a_draft_of_another_question_is_announced_even_if_it_is_not_the_one_reopened(
    qtbot, populated: AppPaths
) -> None:
    first = open_window(qtbot, populated)
    first.show_page(PAGE_CATALOG)
    edit(first, "VENOUS-0004")
    first.catalog_view.editing.editor.stem_edit.setPlainText("სხვა საგნის მონახაზი")
    first.catalog_view.go_to("ALI-0005")  # navigation persists the draft, then another question is shown
    assert first.close()
    second = open_window(qtbot, populated)
    assert second.catalog_view.current_uid == "ALI-0005"
    assert not second.catalog_view.editing.draft_bar.isVisible()  # this question has no draft
    assert second.drafts_label.text() == strings.STATUS_DRAFTS.format(count=1)  # but the program says so
    assert second.catalog_view.editing.banner.label.text() == strings.DR_BANNER.format(count=1)


def test_the_window_stays_open_when_the_draft_cannot_be_written(
    qtbot, window: MainWindow, monkeypatch
) -> None:
    edit(window)
    editor = window.catalog_view.editing.editor
    monkeypatch.setattr(
        window.draft_service,
        "save_draft",
        lambda *a, **k: (_ for _ in ()).throw(StorageFailure(StorageProblem.DISK_FULL, "full")),
    )
    editor.stem_edit.setPlainText("ტექსტი, რომელსაც ადგილი არ ჰპოვა")
    monkeypatch.setattr(editor, "ask_continue_without_saving", lambda detail, closing=False: False)
    assert not window.close()  # refused
    assert window.isVisible() and window.catalog_view.is_editing
    assert editor.stem_edit.toPlainText() == "ტექსტი, რომელსაც ადგილი არ ჰპოვა"
    monkeypatch.undo()  # space is back: the close succeeds and the text is written
    assert window.close()
    assert draft_row(window._paths, UID)["stem"] == "ტექსტი, რომელსაც ადგილი არ ჰპოვა"  # type: ignore[index]


def test_closing_after_a_save_leaves_no_draft_and_one_revision(window: MainWindow) -> None:
    edit(window)
    editor = window.catalog_view.editing.editor
    editor.stem_edit.setPlainText("საბოლოო ტექსტი")
    assert editor.save()
    assert window.close()
    assert draft_row(window._paths, UID) is None
    assert [r["revision_no"] for r in revisions_of(window._paths, UID)] == [1, 2]


def test_keeping_a_draft_then_closing_then_discarding_on_purpose(
    qtbot, populated: AppPaths, monkeypatch
) -> None:
    first = open_window(qtbot, populated)
    first.show_page(PAGE_CATALOG)
    edit(first)
    editor = first.catalog_view.editing.editor
    editor.stem_edit.setPlainText("გადასაგდები ტექსტი")
    wait_state(qtbot, first, EditorState.DRAFT_SAVED)
    monkeypatch.setattr(editor, "ask_cancel_choice", lambda: CancelChoice.KEEP_DRAFT)
    editor.cancel()
    assert first.close()
    second = open_window(qtbot, populated)
    controller = second.catalog_view.editing
    monkeypatch.setattr(controller, "confirm_discard_draft", lambda uid: True)
    controller.draft_bar.discard_button.click()  # the deliberate way to get rid of it
    assert draft_row(populated, UID) is None
    assert second.drafts_label.isHidden()
    assert len(revisions_of(populated, UID)) == 1


def test_the_event_loop_keeps_running_while_the_editor_autosaves(qtbot, window: MainWindow) -> None:
    edit(window)
    editor = window.catalog_view.editing.editor
    ticks: list[int] = []
    from PySide6.QtCore import QTimer

    timer = QTimer(window)
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    for i in range(10):
        editor.stem_edit.setPlainText(f"ტექსტი {i}")
        qtbot.wait(30)
    wait_state(qtbot, window, EditorState.DRAFT_SAVED)
    timer.stop()
    assert len(ticks) > 15  # autosaving never froze the interface
    assert draft_row(window._paths, UID)["stem"] == "ტექსტი 9"  # type: ignore[index]


def test_a_termination_signal_closes_the_window_and_writes_the_draft(qtbot, populated: AppPaths) -> None:
    """``kill`` / Ctrl+C / a closing terminal must not cost the last second of typing."""
    import signal

    from vascqbank_desktop.app.main import install_signal_shutdown

    window = open_window(qtbot, populated)
    window.show_page(PAGE_CATALOG)
    edit(window)
    editor = window.catalog_view.editing.editor
    type_text(editor.stem_edit, "სიგნალამდე აკრეფილი ტექსტი")
    assert editor.autosaver.pending
    previous = install_signal_shutdown(QApplication.instance(), window)
    try:
        signal.raise_signal(signal.SIGTERM)
        qtbot.waitUntil(lambda: not window.isVisible(), timeout=WAIT_MS)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)  # type: ignore[arg-type]
    saved = draft_row(populated, UID)
    assert saved is not None and "სიგნალამდე აკრეფილი ტექსტი" in saved["stem"]
