"""The review workflow on 10,000 synthetic questions: speed and data access patterns.

Budgets are generous (several times the measured values); measured numbers are printed with
``-s``. The bulk set-up writes straight into a *test* database; the application itself has no
bulk approval.
"""

from __future__ import annotations

import statistics
import time

import pytest
from synthetic import write_full_bank
from test_workflow_view import Flow

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import catalog_repository
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.catalog import CatalogFilter
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services import import_service as svc
from vascqbank_desktop.services.approved_service import ApprovedQuestionsService
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.question_stats import bank_counts
from vascqbank_desktop.services.storage import initialize_storage

pytestmark = [pytest.mark.perf, pytest.mark.gui]

WAIT_MS = 20_000
BULK_APPROVED = 2_000


@pytest.fixture(scope="module")
def big_bank(tmp_path_factory) -> AppPaths:
    root = tmp_path_factory.mktemp("review-perf")
    source = root / "synthetic-bank"
    assert write_full_bank(source) == 10_000
    paths = AppPaths(data_dir=root / "data")
    initialize_storage(paths)
    report = svc.dry_run(paths, [source])
    assert len(svc.commit_import(paths, report).created) == 10_000
    # test set-up only: approve every 5th question the way the service would have recorded it
    conn = open_database(paths.database_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE questions SET status = 'APPROVED', approved_revision_id = current_revision_id, "
            "approved_at = '2026-11-18T00:00:00+00:00', lock_version = 1 WHERE id % 5 = 0"
        )
        conn.execute(
            "INSERT INTO review_events (question_id, action, from_status, to_status, revision_id, actor, "
            "created_at) SELECT id, 'APPROVE', 'PENDING_REVIEW', 'APPROVED', current_revision_id, "
            "'ტესტი', '2026-11-18T00:00:00+00:00' FROM questions WHERE status = 'APPROVED'"
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    return paths


@pytest.fixture
def flow(qtbot, styled_qapp, big_bank: AppPaths) -> Flow:
    return Flow(qtbot, big_bank)


def median_ms(fn, repeat: int) -> float:
    samples = []
    for _ in range(repeat):
        started = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - started) * 1000)
    return statistics.median(samples)


def pending_ids(paths: AppPaths, count: int, *, skip: int = 0) -> list[int]:
    ids = CatalogService(paths).query(CatalogFilter(status=ReviewStatus.PENDING_REVIEW)).ids
    return list(ids[skip : skip + count])


def test_single_question_approval_and_reopen_are_fast(big_bank: AppPaths, flow: Flow) -> None:
    catalog = CatalogService(big_bank)
    ids = pending_ids(big_bank, 40, skip=3_000)
    approve_ms, reopen_ms = [], []
    for question_id in ids:
        detail = catalog.detail(question_id)
        started = time.perf_counter()
        approved = flow.review.approve(question_id, detail.lock_version)
        approve_ms.append((time.perf_counter() - started) * 1000)
        started = time.perf_counter()
        flow.review.reopen(question_id, approved.lock_version, "ტესტი")
        reopen_ms.append((time.perf_counter() - started) * 1000)
    median_approve, median_reopen = statistics.median(approve_ms), statistics.median(reopen_ms)
    print(f"\n10k bank: approve {median_approve:.1f} ms, reopen {median_reopen:.1f} ms (median of 40)")
    assert median_approve < 100 and median_reopen < 100


def test_approve_and_next_in_the_gui_is_quick_and_reads_no_revision_history(
    big_bank: AppPaths, flow: Flow, monkeypatch
) -> None:
    ids = pending_ids(big_bank, 30, skip=5_000)
    flow.view.set_status(ReviewStatus.PENDING_REVIEW)
    flow.settle()
    assert flow.view.open_question(ids[0])
    history_reads: list[str] = []
    for name in ("list_revisions", "get_revision"):
        real = getattr(catalog_repository, name)

        def spy(*args, _real=real, _name=name, **kwargs):
            history_reads.append(_name)
            return _real(*args, **kwargs)

        monkeypatch.setattr(catalog_repository, name, spy)
    samples = []
    for expected_next in ids[1:21]:
        started = time.perf_counter()
        assert flow.wf.approve_and_next()
        samples.append((time.perf_counter() - started) * 1000)
        assert flow.view.current_question_id == expected_next  # predictable inside the filtered list
    flow.settle()
    median = statistics.median(samples)
    print(
        f"\n10k bank: approve + navigate in the GUI {median:.1f} ms (median of 20, worst {max(samples):.1f})"
    )
    assert median < 250 and max(samples) < 600
    assert history_reads == []  # ordinary review work never loads revision histories


def test_the_filtered_list_refresh_after_a_decision_is_fast(big_bank: AppPaths, flow: Flow) -> None:
    flow.view.set_status(ReviewStatus.PENDING_REVIEW)
    flow.settle()
    ids = pending_ids(big_bank, 5, skip=7_000)
    assert flow.view.open_question(ids[0])
    started = time.perf_counter()
    assert flow.wf.approve()
    flow.settle()
    refresh_ms = (time.perf_counter() - started) * 1000
    print(f"\n10k bank: decision + filter refresh until the list is settled {refresh_ms:.1f} ms")
    assert refresh_ms < 1000
    assert ids[0] not in flow.view.model.results.ids  # it left the PENDING filter


def test_restoring_a_revision_is_fast(big_bank: AppPaths, flow: Flow) -> None:
    catalog = CatalogService(big_bank)
    question_id = pending_ids(big_bank, 1, skip=6_500)[0]
    samples = []
    for number in range(10):
        detail = catalog.detail(question_id)
        from vascqbank_desktop.domain.question import QuestionContent

        flow.edits.save(
            question_id,
            detail.lock_version,
            QuestionContent(f"ტექსტი {number}", detail.content.options, detail.content.correct, "e"),
        )
    detail = catalog.detail(question_id)
    first = catalog.revisions(question_id)[-1]
    started = time.perf_counter()
    result = flow.review.restore_revision(question_id, detail.lock_version, first.id)
    samples.append((time.perf_counter() - started) * 1000)
    assert result.created_revision and result.revision_no == 12
    history_started = time.perf_counter()
    assert len(catalog.revisions(question_id)) == 12
    history_ms = (time.perf_counter() - history_started) * 1000
    print(f"\n10k bank: restore {samples[0]:.1f} ms, history of 12 revisions {history_ms:.1f} ms")
    assert samples[0] < 150 and history_ms < 50


def test_statistics_and_approved_retrieval_scale(big_bank: AppPaths) -> None:
    stats_ms = median_ms(lambda: bank_counts(big_bank), 10)
    contract = ApprovedQuestionsService(big_bank)
    ids_ms = median_ms(contract.approved_ids, 10)
    count_ms = median_ms(contract.count, 10)
    subject_ms = median_ms(lambda: contract.snapshot("ALI"), 5)
    started = time.perf_counter()
    everything = contract.snapshot()
    snapshot_ms = (time.perf_counter() - started) * 1000
    verify_ms = median_ms(contract.verify, 3)
    approved = bank_counts(big_bank).total_with(ReviewStatus.APPROVED)
    print(
        f"\n10k bank, {approved} approved: statistics {stats_ms:.1f} ms, approved ids {ids_ms:.1f} ms, "
        f"count {count_ms:.1f} ms, one subject {subject_ms:.1f} ms, full snapshot {snapshot_ms:.1f} ms "
        f"({len(everything)} questions), contract check {verify_ms:.1f} ms"
    )
    assert len(everything) == approved >= BULK_APPROVED - 60  # the work of the other tests is included
    assert contract.verify() == []
    assert stats_ms < 100 and ids_ms < 100 and count_ms < 100
    assert subject_ms < 500 and snapshot_ms < 1500 and verify_ms < 500
