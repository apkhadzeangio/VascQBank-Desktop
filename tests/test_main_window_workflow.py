"""Main window and the editorial workflow: statistics, session, shortcuts, restart."""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from bank import events_of, question_row, seed_counts  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_CATALOG  # noqa: E402

pytestmark = pytest.mark.gui

REVIEWER = "რამაზ აფხაძე"


def open_window(qtbot, paths: AppPaths) -> MainWindow:
    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=60)
    qtbot.addWidget(window)
    window.show()
    return window


@pytest.fixture
def populated(app_paths: AppPaths, styled_qapp) -> AppPaths:
    seed_counts(app_paths, {"ALI": 10, "VENOUS": 5})
    ReviewerProfile(SettingsStore(app_paths.settings_file)).set_name(REVIEWER)
    return app_paths


@pytest.fixture
def window(qtbot, populated: AppPaths) -> MainWindow:
    window = open_window(qtbot, populated)
    window.open_subject("ALI")
    window.show_page(PAGE_CATALOG)
    return window


def count_of(label_text: str) -> int:
    return int(label_text.split("<b>")[1].split("</b>")[0].replace(" ", ""))


def strip_counts(window: MainWindow, line: str = "bank_line") -> dict[ReviewStatus, int]:
    row = getattr(window.catalog_view.strip, line)
    return {status: count_of(label.text()) for status, label in row.status_labels.items()}


def test_every_decision_updates_the_global_and_the_per_subject_statistics(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    assert strip_counts(window) == {
        ReviewStatus.PENDING_REVIEW: 15,
        ReviewStatus.IN_REVIEW: 0,
        ReviewStatus.APPROVED: 0,
        ReviewStatus.REJECTED: 0,
    }
    assert view.go_to("ALI-0001")
    assert view.workflow.approve()
    assert strip_counts(window) == {
        ReviewStatus.PENDING_REVIEW: 14,
        ReviewStatus.IN_REVIEW: 0,
        ReviewStatus.APPROVED: 1,
        ReviewStatus.REJECTED: 0,
    }
    assert strip_counts(window, "subject_line")[ReviewStatus.APPROVED] == 1
    view.workflow.ask_reject_reason = lambda *, approved: "მიზეზი"  # type: ignore[method-assign]
    assert view.go_to("ALI-0002") and view.workflow.reject()
    assert view.go_to("VENOUS-0001") and view.workflow.start_review()
    assert strip_counts(window) == {
        ReviewStatus.PENDING_REVIEW: 12,
        ReviewStatus.IN_REVIEW: 1,
        ReviewStatus.APPROVED: 1,
        ReviewStatus.REJECTED: 1,
    }
    view.workflow.ask_reopen_note = lambda status: ""  # type: ignore[method-assign]
    assert view.go_to("ALI-0001") and view.workflow.reopen()
    assert strip_counts(window)[ReviewStatus.APPROVED] == 0
    assert strip_counts(window)[ReviewStatus.IN_REVIEW] == 2
    scope = window.welcome_view.progress_panel
    assert scope.subject_scope.status_labels[ReviewStatus.REJECTED].text() == "1"  # the home page too
    assert scope.bank_scope.status_labels[ReviewStatus.IN_REVIEW].text() == "2"


def test_the_sidebar_and_the_status_bar_stay_consistent(qtbot, window: MainWindow) -> None:
    window.catalog_view.go_to("ALI-0003")
    assert window.catalog_view.workflow.approve()
    assert window.sidebar.subject_count_labels["ALI"].text() == "10 / 900"  # totals are not statuses
    assert window.bank_label.text() == strings.STATUS_BANK.format(total=15)


def test_approve_and_next_is_remembered_as_the_working_position(qtbot, populated: AppPaths) -> None:
    first = open_window(qtbot, populated)
    first.open_subject("ALI")
    first.show_page(PAGE_CATALOG)
    assert first.catalog_view.go_to("ALI-0004")
    assert first.catalog_view.workflow.approve_and_next()
    assert first.catalog_view.current_uid == "ALI-0005"
    assert first.close()
    second = open_window(qtbot, populated)  # the program is started again
    assert second.catalog_view.current_uid == "ALI-0005"  # back at the next question to review
    assert question_row(populated, "ALI-0004")["status"] == "APPROVED"
    assert strip_counts(second)[ReviewStatus.APPROVED] == 1


def test_ctrl_enter_approves_and_moves_on_from_anywhere_in_the_catalog_page(
    qtbot, window: MainWindow
) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0006")
    view.list_view.setFocus()
    qtbot.keyClick(view.list_view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert question_row(window._paths, "ALI-0006")["status"] == "APPROVED"
    assert view.current_uid == "ALI-0007"


def test_the_workflow_does_not_disturb_the_existing_shortcuts(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0002")
    view.detail_view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)  # edit
    assert view.is_editing
    qtbot.keyClick(view.editing.editor.stem_edit, Qt.Key.Key_Escape)  # cancel
    assert not view.is_editing
    view.detail_view.setFocus()
    qtbot.keyClick(view, Qt.Key.Key_H, Qt.KeyboardModifier.ControlModifier)  # history
    assert view.editing.mode.value == "HISTORY"
    view.editing.close_history()
    window.action_next.trigger()  # Alt+Right through the menu action
    assert view.current_uid == "ALI-0003"


def test_the_events_form_a_complete_audit_trail(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0001")
    view.workflow.ask_reject_reason = lambda *, approved: "ა"  # type: ignore[method-assign]
    view.workflow.ask_reopen_note = lambda status: "ბ"  # type: ignore[method-assign]
    assert view.workflow.start_review() and view.workflow.approve()
    assert view.workflow.reject() and view.workflow.reopen() and view.workflow.approve()
    events = events_of(window._paths, "ALI-0001")
    assert [(e["action"], e["from_status"], e["to_status"], e["actor"]) for e in events[1:]] == [
        ("START_REVIEW", "PENDING_REVIEW", "IN_REVIEW", REVIEWER),
        ("APPROVE", "IN_REVIEW", "APPROVED", REVIEWER),
        ("REJECT", "APPROVED", "REJECTED", REVIEWER),
        ("REOPEN", "REJECTED", "IN_REVIEW", REVIEWER),
        ("APPROVE", "IN_REVIEW", "APPROVED", REVIEWER),
    ]
    assert all(e["revision_id"] for e in events)  # every decision names the revision it concerns
