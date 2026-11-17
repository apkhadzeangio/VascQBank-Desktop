"""Import dialog smoke tests on the offscreen Qt platform (synthetic data)."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402
from synthetic import question_block, write_pair  # noqa: E402

from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts  # noqa: E402
from vascqbank_desktop.ui.import_dialog import ImportDialog  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.theme import apply_theme  # noqa: E402

pytestmark = pytest.mark.gui

WAIT_MS = 30_000


@pytest.fixture(scope="module")
def styled_app():
    app = QApplication.instance() or QApplication([])
    register_bundled_fonts()
    app.setFont(build_ui_font())
    apply_theme(app)
    return app


@pytest.fixture
def window(qtbot, styled_app, app_paths):
    win = MainWindow(initialize_storage(app_paths), SUBJECTS)
    qtbot.addWidget(win)
    win.show()
    return win


@pytest.fixture
def source(tmp_path: Path) -> Path:
    folder = tmp_path / "წყარო ფაილები"
    write_pair(folder, "ALI", range(1, 4))
    bad = question_block("VENOUS", 1).replace("*", "") + "\n\n" + question_block("VENOUS", 2) + "\n"
    write_pair(folder, "VENOUS", [1, 2], questions_text=bad)
    (folder / "README.txt").write_text("x", encoding="utf-8")
    return folder


def _dialog(qtbot, window) -> ImportDialog:
    dialog = window.create_import_dialog()
    qtbot.addWidget(dialog)
    dialog.show()
    return dialog


def test_full_import_flow_updates_counts(qtbot, window, source: Path, monkeypatch) -> None:
    assert window.sidebar.subject_count_labels["ALI"].text().startswith("0 /")
    dialog = _dialog(qtbot, window)
    assert not dialog.validate_button.isEnabled()

    dialog.add_paths([source])
    assert dialog.sources_table.rowCount() == 2  # ALI + VENOUS pairs
    assert "README.txt" in dialog.sources_note.text()
    assert dialog.validate_button.isEnabled()

    with qtbot.waitSignal(dialog.validationFinished, timeout=WAIT_MS):
        dialog.run_validation()
    assert dialog.pages.currentIndex() == 1
    assert dialog.summary_values["total_parsed"].text() == "5"
    assert dialog.summary_values["invalid"].text() == "1"
    assert dialog.summary_values["eligible"].text() == "4"
    assert dialog.issues_table.rowCount() >= 1
    assert dialog.issues_table.item(0, 0).text() == "შეცდომა"
    assert dialog.confirm_button.isEnabled()

    monkeypatch.setattr(dialog, "_confirm", lambda count: True)
    with qtbot.waitSignal(dialog.importFinished, timeout=WAIT_MS):
        dialog.confirm_import()
    assert dialog.pages.currentIndex() == 2
    assert dialog.result_values["created"].text() == "4"
    assert dialog.result.backup.path.is_file()

    assert window.sidebar.subject_count_labels["ALI"].text().startswith("3 /")
    assert window.sidebar.subject_count_labels["VENOUS"].text().startswith("1 /")
    assert window.welcome_view.bank_card.isVisibleTo(window)
    assert not window.welcome_view.empty_card.isVisibleTo(window)
    assert "4" in window.bank_label.text()


def test_cancelled_confirmation_imports_nothing(qtbot, window, source: Path, monkeypatch) -> None:
    dialog = _dialog(qtbot, window)
    dialog.add_paths([source])
    with qtbot.waitSignal(dialog.validationFinished, timeout=WAIT_MS):
        dialog.run_validation()
    monkeypatch.setattr(dialog, "_confirm", lambda count: False)
    dialog.confirm_import()
    assert not dialog.is_busy()
    assert dialog.pages.currentIndex() == 1
    assert window.refresh_counts().total == 0


def test_nothing_eligible_disables_import(qtbot, window, source: Path, monkeypatch) -> None:
    first = _dialog(qtbot, window)
    first.add_paths([source])
    with qtbot.waitSignal(first.validationFinished, timeout=WAIT_MS):
        first.run_validation()
    monkeypatch.setattr(first, "_confirm", lambda count: True)
    with qtbot.waitSignal(first.importFinished, timeout=WAIT_MS):
        first.confirm_import()

    second = _dialog(qtbot, window)
    second.add_paths([source])
    with qtbot.waitSignal(second.validationFinished, timeout=WAIT_MS):
        second.run_validation()
    assert second.summary_values["identical_existing"].text() == "4"
    assert second.summary_values["eligible"].text() == "0"
    assert not second.confirm_button.isEnabled()


def test_import_entry_points_exist(window) -> None:
    assert window.action_import.shortcut().toString() == "Ctrl+I"
    assert window.welcome_view.import_button.isVisibleTo(window)
