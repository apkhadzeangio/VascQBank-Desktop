"""Editing in a 10,000-question catalog: speed, responsiveness and integration.

Budgets are generous (several times the measured values) so the test is stable on slow
machines; measured numbers are printed with ``-s``. All text is synthetic filler.
"""

from __future__ import annotations

import statistics
import time

import pytest
from synthetic import write_full_bank

pytest.importorskip("PySide6")

from bank import draft_row, revisions_of  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from test_editor_view import Env  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.catalog import CatalogFilter  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.services import import_service as svc  # noqa: E402
from vascqbank_desktop.services.catalog_service import CatalogService  # noqa: E402
from vascqbank_desktop.services.draft_service import DraftService  # noqa: E402
from vascqbank_desktop.services.question_stats import bank_counts  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui.question_editor_view import EditorState  # noqa: E402

pytestmark = [pytest.mark.perf, pytest.mark.gui]

WAIT_MS = 20_000


@pytest.fixture(scope="module")
def big_bank(tmp_path_factory) -> AppPaths:
    root = tmp_path_factory.mktemp("edit-perf")
    source = root / "synthetic-bank"
    assert write_full_bank(source) == 10_000
    paths = AppPaths(data_dir=root / "data")
    initialize_storage(paths)
    report = svc.dry_run(paths, [source])
    assert len(svc.commit_import(paths, report).created) == 10_000
    ReviewerProfile(SettingsStore(paths.settings_file)).set_name("რამაზ აფხაძე")
    return paths


@pytest.fixture
def env(qtbot, styled_qapp, big_bank: AppPaths) -> Env:
    return Env(qtbot, big_bank)


def median_ms(fn, repeat: int) -> float:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


def test_editor_open_draft_write_and_save_are_fast_in_a_10k_bank(env: Env) -> None:
    catalog = CatalogService(env.paths)
    ids = catalog.query(CatalogFilter()).ids
    assert len(ids) == 10_000
    deep = ids[7_500]
    assert env.view.open_question(deep)
    started = time.perf_counter()
    assert env.view.start_editing()
    open_ms = (time.perf_counter() - started) * 1000
    editor = env.editor
    session = editor.session
    assert session is not None

    def one_draft_write() -> None:
        env.drafts.save_draft(
            session.question_id,
            session.base_revision_id,
            session.base_lock_version,
            editor.content(),
            "რამაზ აფხაძე",
        )

    draft_ms = median_ms(one_draft_write, 20)
    editor.stem_edit.setPlainText("შენახული ტექსტი 10k ბანკში")
    started = time.perf_counter()
    assert editor.save()
    save_ms = (time.perf_counter() - started) * 1000
    print(f"\n10k bank: open editor {open_ms:.1f} ms, draft write {draft_ms:.1f} ms, save {save_ms:.1f} ms")
    assert open_ms < 500 and draft_ms < 200 and save_ms < 500


def test_an_edit_deep_in_the_catalog_updates_search_counts_and_leaves_everything_else(env: Env) -> None:
    catalog = CatalogService(env.paths)
    ids = catalog.query(CatalogFilter()).ids
    target, neighbour = ids[4_321], ids[4_322]
    before = catalog.detail(neighbour)
    counts_before = bank_counts(env.paths)
    assert env.view.open_question(target)
    uid = env.view.current_uid
    assert uid is not None
    assert env.view.start_editing()
    unique = "უნიკალურიტერმინიპერფორაციის"
    env.editor.explanation_edit.setPlainText(f"ახალი განმარტება {unique} ასევე")
    assert env.editor.save()
    env.view.editing.editor.cancel()
    counts_after = bank_counts(env.paths)
    assert counts_after.total == counts_before.total == 10_000
    assert (
        counts_after.total_with(ReviewStatus.IN_REVIEW)
        == counts_before.total_with(ReviewStatus.IN_REVIEW) + 1
    )
    assert (
        counts_after.total_with(ReviewStatus.PENDING_REVIEW)
        == counts_before.total_with(ReviewStatus.PENDING_REVIEW) - 1
    )
    hits = catalog.query(CatalogFilter(text=unique))
    found = [d.uid for d in (catalog.detail(i) for i in hits.ids) if d is not None]
    assert found == [uid]  # the edited text is searchable straight away
    assert catalog.detail(neighbour) == before  # nothing next to it moved
    assert len(revisions_of(env.paths, uid)) == 2


def test_hundreds_of_drafts_list_quickly_and_are_marked_in_the_catalog(env: Env) -> None:
    catalog = CatalogService(env.paths)
    ids = catalog.query(CatalogFilter()).ids[9_000:9_300]
    drafts = DraftService(env.paths)
    for question_id in ids:
        detail = catalog.detail(question_id)
        assert detail is not None
        drafts.save_draft(detail.id, detail.revision_id, detail.lock_version, detail.content, "რამაზ აფხაძე")
    list_ms = median_ms(drafts.list_drafts, 3)
    ids_ms = median_ms(drafts.draft_question_ids, 10)
    started = time.perf_counter()
    env.controller.refresh_drafts_state()
    refresh_ms = (time.perf_counter() - started) * 1000
    started = time.perf_counter()
    dialog = env.controller.open_draft_list()
    dialog_ms = (time.perf_counter() - started) * 1000
    print(
        f"\n300 drafts: list {list_ms:.1f} ms, ids {ids_ms:.1f} ms, refresh {refresh_ms:.1f} ms, "
        f"dialog {dialog_ms:.1f} ms"
    )
    assert dialog.table.rowCount() >= 300
    assert env.controller.banner.label.text().endswith(str(len(drafts.draft_question_ids())))
    assert list_ms < 1000 and ids_ms < 100 and refresh_ms < 500 and dialog_ms < 2000
    dialog.close()


def test_the_interface_never_stalls_while_typing_and_autosaving_in_a_10k_bank(env: Env, qtbot) -> None:
    catalog = CatalogService(env.paths)
    assert env.view.open_question(catalog.query(CatalogFilter()).ids[2_000])
    assert env.view.start_editing()
    gaps: list[float] = []
    last = [time.perf_counter()]

    def tick() -> None:
        now = time.perf_counter()
        gaps.append(now - last[0])
        last[0] = now

    timer = QTimer(env.view)
    timer.setInterval(5)
    timer.timeout.connect(tick)
    timer.start()
    for i in range(60):
        env.editor.stem_edit.setPlainText(f"ტექსტი {i} " + "ა" * 4000)
        qtbot.wait(20)
    qtbot.waitUntil(lambda: env.editor.state is EditorState.DRAFT_SAVED, timeout=WAIT_MS)
    timer.stop()
    worst_ms = max(gaps) * 1000
    print(f"\nworst event-loop gap while typing and autosaving: {worst_ms:.1f} ms")
    assert worst_ms < 400
    uid = env.view.current_uid
    assert uid is not None and draft_row(env.paths, uid) is not None
