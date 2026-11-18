"""The export page: preview, background export with progress, cancellation, errors, closing.

Waiting happens in a real ``QEventLoop`` (not ``qtbot.wait``, which keeps the GIL and starves the
export worker — see ARCHITECTURE.md §17.6).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from exportbank import build  # noqa: E402
from PySide6.QtCore import QEventLoop, QTimer  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services import export_service  # noqa: E402
from vascqbank_desktop.services.edit_errors import StorageFailure, StorageProblem  # noqa: E402
from vascqbank_desktop.services.export_service import ExportService, ExportStorageError  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.export_view import ExportView  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_EXPORT  # noqa: E402

pytestmark = pytest.mark.gui


def pump(ms: int) -> None:
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def wait_for(condition, timeout_s: float = 30.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not condition():
        assert time.monotonic() < deadline, "timed out"
        pump(20)


@pytest.fixture
def view(qtbot, styled_qapp, app_paths: AppPaths, tmp_path: Path):
    build(app_paths)
    page = ExportView(ExportService(app_paths), SUBJECTS, preview_delay_ms=10)
    qtbot.addWidget(page)
    page.resize(1200, 900)
    page.show()
    page.destination_edit.setText(str(tmp_path / "exports"))
    page.select_subjects(["ALI", "VENOUS"])
    wait_for(lambda: page.plan is not None)
    yield page
    page.shutdown()  # as the main window does on close: no export or preview left running


def test_the_preview_shows_what_will_be_exported(view: ExportView) -> None:
    text = view.summary_label.text()
    assert strings.EX_P_APPROVED.format(count="6") in text
    assert strings.EX_P_EXCLUDED.format(count="5") in text
    assert "ALI, VENOUS" in text and "განხილვის ვერსია" in text
    assert view.files_view.toPlainText().split("\n") == [
        "DOCX/ALI_review.docx",
        "DOCX/VENOUS_review.docx",
        "export-manifest.json",
    ]
    assert view.export_button.isEnabled() and not view.problem_label.isVisible()


def test_choices_change_the_preview(view: ExportView) -> None:
    view.txt_check.setChecked(True)
    view.exam_check.setChecked(True)
    view.key_check.setChecked(True)
    view.combined_check.setChecked(True)
    wait_for(lambda: view.plan is not None)
    files = view.files_view.toPlainText().split("\n")
    assert "DOCX/VascQBank_All_Approved_exam.docx" in files and "TXT/VENOUS_explanations.txt" in files
    assert len(files) == 3 * 3 + 4 + 1
    view.docx_check.setChecked(False)
    view.txt_check.setChecked(False)
    wait_for(lambda: view.summary_label.text() == strings.EX_NOTHING_SELECTED)
    assert not view.export_button.isEnabled()


def test_a_selection_without_approved_questions_cannot_be_exported(view: ExportView) -> None:
    view.select_subjects(["PAD_CLTI"])
    wait_for(lambda: view.plan is not None)
    assert view.problem_label.isVisible() and view.problem_label.text() == strings.EX_EMPTY
    assert not view.export_button.isEnabled() and not view.start_export()


def test_txt_problems_are_named_and_block_only_the_txt(view: ExportView, app_paths: AppPaths) -> None:
    from exportbank import Bank

    bank = Bank(app_paths)
    bank.edit("ALI-0021", stem="აბზაცი\n\nმეორე აბზაცი")
    bank.approve("ALI-0021")
    view.txt_check.setChecked(True)
    wait_for(lambda: view.plan is not None)
    assert view.problem_label.isVisible() and "ALI-0021" in view.problem_label.text()
    assert "ცარიელი სტრიქონია" in view.problem_label.text() and not view.export_button.isEnabled()
    view.txt_check.setChecked(False)
    wait_for(lambda: view.plan is not None)
    assert view.export_button.isEnabled()


def test_the_export_runs_in_the_background_and_reports_success(view: ExportView, tmp_path: Path) -> None:
    view.txt_check.setChecked(True)
    view.exam_check.setChecked(True)
    wait_for(lambda: view.plan is not None)
    ticks: list[float] = []
    timer = QTimer(view)
    timer.setInterval(10)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    phases: list[str] = []
    assert view.start_export()
    assert view.is_running() and not view.export_button.isEnabled() and view.cancel_button.isEnabled()
    assert not view.subject_list.isEnabled()  # choices are frozen while it runs
    wait_for(lambda: (phases.append(view.phase_label.text()) or True) and not view.is_running())
    timer.stop()
    result = view.last_result
    assert result is not None and result.folder.is_dir() and result.approved == 6
    assert view.result_label.objectName() == "SuccessNotice" and "✓" in view.result_label.text()
    assert view.progress_bar.value() == 100 and view.open_button.isEnabled()
    assert any(strings.EX_PHASES["DOCX"] in p for p in phases)
    gaps = [b - a for a, b in zip(ticks, ticks[1:], strict=False)]
    assert gaps and max(gaps) < 1.0  # the window kept responding while documents were written


def test_cancelling_publishes_nothing(view: ExportView, tmp_path: Path, monkeypatch) -> None:
    real = export_service.build_docx

    def slow_build(*args, **kwargs):
        time.sleep(0.3)
        return real(*args, **kwargs)

    monkeypatch.setattr(export_service, "build_docx", slow_build)
    assert view.start_export()
    pump(50)
    view.cancel_button.click()
    wait_for(lambda: not view.is_running())
    assert view.result_label.text() == strings.EX_CANCELLED and view.last_result is None
    exports = tmp_path / "exports"
    assert not exports.exists() or list(exports.iterdir()) == []
    wait_for(lambda: view.plan is not None)
    assert view.export_button.isEnabled()  # ready for another try


def test_a_failure_is_shown_in_georgian_and_nothing_claims_success(view: ExportView, monkeypatch) -> None:
    def fail(*args, **kwargs):
        raise ExportStorageError(StorageFailure(StorageProblem.DISK_FULL, "full"))

    monkeypatch.setattr(view._service, "run", fail)
    assert view.start_export()
    wait_for(lambda: not view.is_running())
    assert view.result_label.objectName() == "ErrorNotice"
    assert (
        "დისკზე ადგილი არ არის" in view.result_label.text()
        and "არაფერი გამოქვეყნებულა" in view.result_label.text()
    )
    assert view.last_result is None and not view.open_button.isEnabled()
    assert view.subject_list.isEnabled()


def test_closing_the_window_during_an_export_asks_and_cleans_up(
    qtbot, styled_qapp, app_paths: AppPaths, tmp_path: Path, monkeypatch
) -> None:
    build(app_paths)
    window = MainWindow(initialize_storage(app_paths), SUBJECTS)
    qtbot.addWidget(window)
    window.show()
    window.show_page(PAGE_EXPORT)
    page = window.export_view
    page.destination_edit.setText(str(tmp_path / "exports"))
    page.select_subjects(["ALI", "VENOUS"])
    wait_for(lambda: page.plan is not None)
    real = export_service.build_docx

    def slow_build(*args, **kwargs):
        time.sleep(0.4)
        return real(*args, **kwargs)

    monkeypatch.setattr(export_service, "build_docx", slow_build)
    assert page.start_export()
    page.ask_stop_on_close = lambda: False  # type: ignore[method-assign]
    assert not window.close() and window.isVisible() and page.is_running()  # the reviewer lets it finish
    page.ask_stop_on_close = lambda: True  # type: ignore[method-assign]
    assert window.close()
    assert not page.is_running()
    exports = tmp_path / "exports"
    assert not exports.exists() or [p.name for p in exports.iterdir() if not p.name.startswith(".")] == []


def test_the_menu_opens_the_export_page(qtbot, styled_qapp, app_paths: AppPaths) -> None:
    build(app_paths)
    window = MainWindow(initialize_storage(app_paths), SUBJECTS)
    qtbot.addWidget(window)
    window.show()
    window.action_export.trigger()
    assert window.pages.currentWidget() is window.export_view
    assert window.action_export.shortcut().toString() == "Ctrl+Shift+E"
    assert window.sidebar.nav_list.currentItem().text() == strings.NAV_EXPORT
