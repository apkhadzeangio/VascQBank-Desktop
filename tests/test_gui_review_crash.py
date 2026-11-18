"""The whole GUI killed (SIGKILL; TerminateProcess on Windows) in the middle of an approval or a restore.

A child copy of the program runs on a TEMPORARY database of synthetic questions and dies at an
exact SQL statement of the decision's transaction. The next start must find the question
exactly as it was — no half approval — and the reviewer must be able to repeat the decision.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6")

from bank import events_of, question_row, revisions_of, seed_counts, snapshot  # noqa: E402
from crash import CHILD_DIE, KILLED_RETURNCODE  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_CATALOG  # noqa: E402

pytestmark = pytest.mark.gui

UID = "ALI-0003"

CHILD = CHILD_DIE + textwrap.dedent(
    """
    import os, signal, sys
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.domain.subjects import SUBJECTS
    from vascqbank_desktop.services import review_service
    from vascqbank_desktop.services.storage import initialize_storage
    from vascqbank_desktop.ui.main_window import MainWindow

    paths = AppPaths(data_dir=Path(sys.argv[1]))
    scenario, kill_at = sys.argv[2], sys.argv[3]
    app = QApplication([])
    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=80)
    window.show()
    window.show_page("catalog")
    view = window.catalog_view
    assert view.go_to("ALI-0003")
    real_open = review_service.open_database

    def armed(path):
        conn = real_open(path)

        def trace(statement):
            if kill_at in statement:
                die()  # die inside the decision's transaction

        conn.set_trace_callback(trace)
        return conn

    review_service.open_database = armed
    if scenario == "approve_and_next":
        view.workflow.approve_and_next()
    elif scenario == "restore":
        assert view.show_history()
        view.editing.history.select_revision_number(1)
        view.workflow.ask_restore = lambda *a, **k: __import__(
            "vascqbank_desktop.ui.catalog_workflow", fromlist=["RestoreChoice"]).RestoreChoice.RESTORE
        view.editing.history.restore_button.click()
    print("SURVIVED", flush=True)
    """
)


def run_child(paths: AppPaths, scenario: str, kill_at: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    return subprocess.run(
        [sys.executable, "-c", CHILD, str(paths.data_dir), scenario, kill_at],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        timeout=120,
    )


@pytest.fixture
def bank(app_paths: AppPaths, styled_qapp) -> AppPaths:
    seed_counts(app_paths, {"ALI": 8})
    ReviewerProfile(SettingsStore(app_paths.settings_file)).set_name("რამაზ აფხაძე")
    return app_paths


def restart(qtbot, paths: AppPaths) -> MainWindow:
    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=60)
    qtbot.addWidget(window)
    window.show()
    window.show_page(PAGE_CATALOG)
    return window


def sound(paths: AppPaths) -> bool:
    conn = sqlite3.connect(paths.database_file)
    try:
        return (
            conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            and conn.execute("PRAGMA foreign_key_check").fetchall() == []
        )
    finally:
        conn.close()


@pytest.mark.parametrize("kill_at", ["UPDATE questions SET status", "INSERT INTO review_events", "COMMIT"])
def test_a_killed_approve_and_next_leaves_the_question_pending_and_can_be_repeated(
    qtbot, bank: AppPaths, kill_at: str
) -> None:
    before = snapshot(bank)
    result = run_child(bank, "approve_and_next", kill_at)
    assert result.returncode == KILLED_RETURNCODE and "SURVIVED" not in result.stdout, result.stderr
    assert sound(bank) and snapshot(bank) == before  # no half approval, no event, no lock bump

    window = restart(qtbot, bank)
    view = window.catalog_view
    assert view.go_to(UID) and view.detail_view.detail.status.value == "PENDING_REVIEW"
    assert view.workflow.approve_and_next()  # the decision can simply be made again
    assert question_row(bank, UID)["status"] == "APPROVED" and view.current_uid == "ALI-0004"
    assert [e["action"] for e in events_of(bank, UID)] == ["IMPORTED", "APPROVE"]


@pytest.mark.parametrize(
    "kill_at", ["INSERT INTO question_revisions", "UPDATE questions SET current_revision_id"]
)
def test_a_killed_restore_leaves_no_new_revision(qtbot, bank: AppPaths, kill_at: str) -> None:
    from vascqbank_desktop.domain.question import QuestionContent
    from vascqbank_desktop.services.catalog_service import CatalogService
    from vascqbank_desktop.services.edit_service import EditService

    profile = ReviewerProfile(SettingsStore(bank.settings_file))
    detail = CatalogService(bank).detail_by_uid(UID)
    EditService(bank, profile).save(
        detail.id,
        detail.lock_version,
        QuestionContent("მეორე რედაქცია", detail.content.options, detail.content.correct, "e"),
    )
    before = snapshot(bank)
    result = run_child(bank, "restore", kill_at)
    assert result.returncode == KILLED_RETURNCODE and "SURVIVED" not in result.stdout, result.stderr
    assert sound(bank) and snapshot(bank) == before
    assert [r["revision_no"] for r in revisions_of(bank, UID)] == [1, 2]
