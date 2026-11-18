"""Phase 8 on 10,000 synthetic questions: navigation, quick filters, search, editing, approve-and-
next, backup listing/verification/restore and the export page. Budgets are generous (several times
the measured values); measured numbers are printed with ``-s``.
"""

from __future__ import annotations

import statistics
import time

import pytest
from synthetic import write_full_bank

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import SUBJECTS
from vascqbank_desktop.services import import_service as svc
from vascqbank_desktop.services import restore_service
from vascqbank_desktop.services.backup_service import create_manual_backup, list_backup_entries, verify_entry
from vascqbank_desktop.services.question_stats import bank_counts
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore
from vascqbank_desktop.services.storage import initialize_storage
from vascqbank_desktop.ui.main_window import MainWindow
from vascqbank_desktop.ui.sidebar import PAGE_CATALOG, PAGE_EXPORT

pytestmark = [pytest.mark.perf, pytest.mark.gui]
WAIT_MS = 60_000
RESULTS: dict[str, float] = {}


@pytest.fixture(scope="module")
def big_bank(tmp_path_factory) -> AppPaths:
    root = tmp_path_factory.mktemp("phase8-perf")
    source = root / "synthetic-bank"
    assert write_full_bank(source) == 10_000
    paths = AppPaths(data_dir=root / "data")
    initialize_storage(paths)
    assert len(svc.commit_import(paths, svc.dry_run(paths, [source])).created) == 10_000
    conn = open_database(paths.database_file)  # test set-up only: a realistic mix of decisions
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE questions SET status = 'APPROVED', approved_revision_id = current_revision_id, "
            "approved_at = '2026-11-18T00:00:00+00:00', lock_version = 1 WHERE id % 7 = 0"
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    ReviewerProfile(SettingsStore(paths.settings_file)).set_name("რამაზ აფხაძე")
    return paths


def timed(name: str, fn, *, repeat: int = 1) -> float:  # noqa: ANN001
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    value = statistics.median(samples)
    RESULTS[name] = value
    print(f"{name}: {value:.1f} ms")
    return value


@pytest.fixture
def window(qtbot, styled_qapp, big_bank: AppPaths) -> MainWindow:
    window = MainWindow(initialize_storage(big_bank), SUBJECTS, autosave_delay_ms=200)
    qtbot.addWidget(window)
    window.show()
    window.show_page(PAGE_CATALOG)
    window.wait_for_background_work()
    return window


def test_reviewer_workspace_on_10k(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("PAD_CLTI-0001")
    assert timed("next question (median of 50)", view.next_question, repeat=50) < 60
    for index in range(6):
        assert timed(f"quick filter {index + 1}", lambda i=index: view.set_quick_filter(i)) < 250
    view.set_quick_filter(0)
    view.set_search_text("სატესტო ტექსტი ALI-77")
    started = time.perf_counter()
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    RESULTS["search (debounce included)"] = (time.perf_counter() - started) * 1000
    print(f"search (debounce included): {RESULTS['search (debounce included)']:.1f} ms")
    assert len(view.model.results) >= 1
    view.set_search_text("")
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=WAIT_MS)
    assert timed("counts incl. drafts per subject", lambda: bank_counts(window._paths)) < 150


def test_editing_and_approve_and_next_on_10k(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    view.set_quick_filter(1)  # pending
    assert view.go_to("VENOUS-0010")
    assert timed("open editor", view.start_editing) < 400
    view.editing.editor.stem_edit.setPlainText("სინთეზური შესწორება წარმადობის ტესტისთვის")
    assert timed("save revision", view.editing.editor.save) < 600
    assert view.leave_editing()
    assert timed("approve and next (median of 10)", view.workflow.approve_and_next, repeat=10) < 600
    assert window.catalog_service.detail_by_uid("VENOUS-0010").status is ReviewStatus.APPROVED


def test_backups_and_restore_on_10k(qtbot, window: MainWindow, big_bank: AppPaths) -> None:
    entry = None

    def create() -> None:
        nonlocal entry
        entry = create_manual_backup(big_bank)

    assert timed("create backup", create) < 5_000
    for _ in range(4):
        create_manual_backup(big_bank)
    assert timed("list backups", lambda: list_backup_entries(big_bank), repeat=5) < 200
    assert entry is not None
    assert timed("verify one backup", lambda: verify_entry(entry)) < 3_000
    summary = None

    def inspect() -> None:
        nonlocal summary
        summary = restore_service.inspect_backup(entry.path)

    assert timed("restore pre-check (inspect)", inspect) < 3_000
    assert window.prepare_for_restore() is None
    result = None

    def restore() -> None:
        nonlocal result
        result = restore_service.restore_backup(big_bank, entry.path, expected_sha256=summary.sha256)

    assert timed("restore (safety backup + copy + publish + reopen)", restore) < 15_000
    for name, seconds in result.timings.items():
        print(f"  restore step {name}: {seconds * 1000:.1f} ms")
    assert timed("window reload after restore", lambda: window.apply_restore(result)) < 3_000
    assert window.refresh_counts().total == 10_000


def test_export_page_loading_on_10k(qtbot, window: MainWindow) -> None:
    view = window.export_view

    def load() -> None:
        window.show_page(PAGE_EXPORT)
        qtbot.waitUntil(lambda: view.plan is not None and not view.is_previewing(), timeout=WAIT_MS)

    assert timed("export page with preview", load) < 5_000
