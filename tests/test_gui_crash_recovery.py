"""The whole program killed abruptly (SIGKILL; TerminateProcess on Windows) while a question is being edited.

Each scenario starts a *child* copy of the GUI (offscreen) on a TEMPORARY database of synthetic
questions, drives it to a precise state, kills only that child, and then starts the main window
again in the test process to see what the next start offers. The kill is never aimed at a
process that holds real data: the child can only open the temporary data directory it is given.
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6")

from bank import draft_row, question_row, revisions_of, seed_counts  # noqa: E402
from crash import CHILD_DIE, KILLED_RETURNCODE, kill  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_CATALOG  # noqa: E402

pytestmark = pytest.mark.gui

UID = "ALI-0003"
FIRST = "პირველი მონახაზი — დადასტურებულად ჩაწერილი"
SECOND = "მეორე მონახაზი — ვერ მოასწრო ჩაწერა"

CHILD = CHILD_DIE + textwrap.dedent(
    """
    import os, signal, sys, time
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from pathlib import Path
    from PySide6.QtWidgets import QApplication
    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.domain.subjects import SUBJECTS
    from vascqbank_desktop.services import draft_service
    from vascqbank_desktop.services.storage import initialize_storage
    from vascqbank_desktop.ui.main_window import MainWindow
    from vascqbank_desktop.ui.question_editor_view import EditorState

    paths = AppPaths(data_dir=Path(sys.argv[1]))
    scenario, first, second = sys.argv[2], sys.argv[3], sys.argv[4]
    app = QApplication([])
    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=80)
    window.show()
    window.show_page("catalog")
    view = window.catalog_view
    assert view.go_to("ALI-0003") and view.start_editing()
    editor = view.editing.editor

    def pump_until(condition, seconds=15):
        end = time.monotonic() + seconds
        while not condition():
            app.processEvents()
            time.sleep(0.005)
            if time.monotonic() > end:
                print("TIMEOUT", flush=True)
                os._exit(3)

    editor.stem_edit.setPlainText(first)
    pump_until(lambda: editor.state is EditorState.DRAFT_SAVED)

    if scenario == "saved_draft":
        print("READY", flush=True)
        time.sleep(60)  # waits for the kill
    elif scenario == "unsaved_typing":
        editor.stem_edit.setPlainText(second)
        print("READY", flush=True)
        time.sleep(60)  # the event loop is blocked: the autosave pause can never elapse
    elif scenario == "kill_inside_write":
        real_open = draft_service.open_database

        def armed(path):
            conn = real_open(path)

            def trace(statement):
                if "drafts" in statement and not statement.lstrip().upper().startswith("SELECT"):
                    die()  # die inside the draft transaction

            conn.set_trace_callback(trace)
            return conn

        draft_service.open_database = armed
        editor.stem_edit.setPlainText(second)
        pump_until(lambda: False, seconds=20)
    """
)


@pytest.fixture
def bank(app_paths: AppPaths, styled_qapp) -> AppPaths:
    seed_counts(app_paths, {"ALI": 12, "VENOUS": 6})
    ReviewerProfile(SettingsStore(app_paths.settings_file)).set_name("რამაზ აფხაძე")
    return app_paths


def run_child(paths: AppPaths, scenario: str) -> subprocess.Popen[str]:
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
    return subprocess.Popen(
        [sys.executable, "-c", CHILD, str(paths.data_dir), scenario, FIRST, SECOND],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        env=env,
    )


def wait_ready_and_kill(child: subprocess.Popen[str]) -> None:
    try:
        assert child.stdout is not None
        line = child.stdout.readline().strip()
        assert line == "READY", (line, child.stderr.read() if child.stderr else "")
        kill(child.pid)
    finally:
        child.wait(timeout=30)
    assert child.returncode == KILLED_RETURNCODE


def database_is_sound(paths: AppPaths) -> None:
    conn = sqlite3.connect(paths.database_file)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def restart(qtbot, paths: AppPaths) -> MainWindow:
    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=60)
    qtbot.addWidget(window)
    window.show()
    window.show_page(PAGE_CATALOG)
    return window


def test_a_confirmed_draft_survives_the_program_being_killed(qtbot, bank: AppPaths) -> None:
    wait_ready_and_kill(run_child(bank, "saved_draft"))
    database_is_sound(bank)
    row = draft_row(bank, UID)
    assert row is not None and row["stem"] == FIRST
    assert len(revisions_of(bank, UID)) == 1 and question_row(bank, UID)["status"] == "PENDING_REVIEW"

    window = restart(qtbot, bank)
    view = window.catalog_view
    assert window.drafts_label.text() == strings.STATUS_DRAFTS.format(count=1)
    assert view.go_to(UID)
    assert view.editing.draft_bar.isVisible()  # offered, never applied silently
    assert FIRST not in view.detail_view.stem_label.text()
    assert view.start_editing()
    assert view.editing.editor.stem_edit.toPlainText() == FIRST
    assert view.editing.editor.save()  # and the reviewer can finish the work
    assert revisions_of(bank, UID)[-1]["stem"] == FIRST
    assert draft_row(bank, UID) is None


def test_text_typed_inside_the_last_pause_may_be_lost_but_never_corrupts_anything(
    qtbot, bank: AppPaths
) -> None:
    """The documented limit of autosave: what was typed after the last confirmed write is not
    on disk. What *is* on disk must be exactly the previous confirmed draft."""
    wait_ready_and_kill(run_child(bank, "unsaved_typing"))
    database_is_sound(bank)
    row = draft_row(bank, UID)
    assert row is not None and row["stem"] == FIRST  # the earlier confirmed text, intact
    assert SECOND not in str(row)
    window = restart(qtbot, bank)
    assert window.drafts_label.text() == strings.STATUS_DRAFTS.format(count=1)


def test_killed_in_the_middle_of_a_draft_write_leaves_the_previous_draft_intact(
    qtbot, bank: AppPaths
) -> None:
    child = run_child(bank, "kill_inside_write")
    child.wait(timeout=60)
    assert child.returncode == KILLED_RETURNCODE, child.stderr.read() if child.stderr else ""
    database_is_sound(bank)
    row = draft_row(bank, UID)
    assert row is not None and row["stem"] == FIRST  # the half-finished write left no trace
    assert row["write_count"] == 1
    assert len(revisions_of(bank, UID)) == 1
    window = restart(qtbot, bank)
    assert window.catalog_view.go_to(UID)
    assert window.catalog_view.editing.draft_bar.isVisible()
