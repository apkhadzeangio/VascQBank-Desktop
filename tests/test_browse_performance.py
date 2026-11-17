"""Browsing and searching 10,000 synthetic questions: timings, query shapes and memory.

Budgets are deliberately generous (several times the measured values) so the test is stable on
slow machines; the measured numbers are printed (run with ``-s``) and recorded as test properties.
All data is synthetic filler text — no real question content.
"""

from __future__ import annotations

import gc
import os
import random
import sqlite3
import statistics
import time
import tracemalloc
from pathlib import Path

import pytest
from synthetic import write_full_bank

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.catalog import CatalogFilter, SearchMode, SortOrder
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import SUBJECTS
from vascqbank_desktop.services import import_service as svc
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.storage import initialize_storage

pytestmark = pytest.mark.perf

PAGE = 100


def rss_mb() -> float | None:
    """Resident set size in MB (Linux only; ``None`` elsewhere)."""
    try:
        for line in Path("/proc/self/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    except OSError:
        return None
    return None


def timed(fn, repeat: int = 1) -> tuple[float, object]:
    """Median seconds over *repeat* runs, and the last result."""
    samples: list[float] = []
    result = None
    for _ in range(repeat):
        started = time.perf_counter()
        result = fn()
        samples.append(time.perf_counter() - started)
    return statistics.median(samples), result


@pytest.fixture(scope="module")
def bank(tmp_path_factory) -> AppPaths:
    root = tmp_path_factory.mktemp("browse-perf")
    source = root / "synthetic-bank"
    assert write_full_bank(source) == 10_000
    paths = AppPaths(data_dir=root / "data")
    initialize_storage(paths)
    report = svc.dry_run(paths, [source])
    assert len(svc.commit_import(paths, report).created) == 10_000
    # give a realistic spread of statuses so filters are not trivially one-sided
    conn = sqlite3.connect(paths.database_file)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("UPDATE questions SET status = 'IN_REVIEW' WHERE id % 7 = 0")
    conn.execute("UPDATE questions SET status = 'REJECTED' WHERE id % 13 = 0 AND status = 'PENDING_REVIEW'")
    conn.execute(
        "UPDATE questions SET status = 'APPROVED', approved_revision_id = current_revision_id, approved_at = "
        "'2026-11-17T00:00:00+00:00' WHERE id % 11 = 0 AND status = 'PENDING_REVIEW'"
    )
    conn.commit()
    conn.close()
    return paths


def test_catalog_service_timings_at_ten_thousand(bank: AppPaths, record_property) -> None:
    service = CatalogService(bank)
    timings: dict[str, float] = {}

    # --- listing -------------------------------------------------------------------------------
    timings["catalog_load_all_ids+first_page"], loaded = timed(
        lambda: (lambda r: (r, service.rows(r.ids[:PAGE])))(service.query(CatalogFilter())), repeat=5
    )
    results, first_page = loaded
    assert len(results) == 10_000 and len(first_page) == PAGE
    assert first_page[0].uid == "PAD_CLTI-0001" and results.mode is SearchMode.BROWSE

    switch_times = []
    for subject in SUBJECTS:
        seconds, (subject_results, rows) = timed(
            lambda s=subject: (lambda r: (r, service.rows(r.ids[:PAGE])))(
                service.query(CatalogFilter(subject_code=s.code))
            )
        )
        assert len(subject_results) == s_target(subject) and len(rows) == min(PAGE, s_target(subject))
        switch_times.append(seconds)
    timings["subject_switch_median"] = statistics.median(switch_times)
    timings["subject_switch_worst"] = max(switch_times)

    timings["status_filter"], approved = timed(
        lambda: service.query(CatalogFilter(status=ReviewStatus.APPROVED)), repeat=5
    )
    assert 0 < len(approved) < 10_000
    timings["sort_by_status"], by_status = timed(
        lambda: service.query(CatalogFilter(sort=SortOrder.STATUS)), repeat=5
    )
    assert len(by_status) == 10_000
    timings["count_subject_status"], _ = timed(
        lambda: service.count("PAD_CLTI", ReviewStatus.PENDING_REVIEW), repeat=5
    )

    # --- opening questions -----------------------------------------------------------------------------
    ids = random.Random(7).sample(list(results.ids), 300)
    timings["open_detail_median"], _ = timed(lambda: [service.detail(i) for i in ids[:50]], repeat=3)
    timings["open_detail_median"] /= 50
    timings["locate_by_id"], location = timed(
        lambda: service.resolve_reference("DIALYSIS_ACCESS-0500"), repeat=10
    )
    assert location.uid == "DIALYSIS_ACCESS-0500"

    # --- search --------------------------------------------------------------------------------------------
    timings["search_like_first_scan"], like_first = timed(
        lambda: CatalogService(bank, use_fts=False).query(CatalogFilter(text="სატესტო ტექსტი ALI-1"))
    )
    timings["index_build_10k"], status = timed(service.ensure_search_index)
    assert status.available and status.rebuilt and status.indexed == 10_000
    timings["index_refresh_nothing_to_do"], again = timed(service.ensure_search_index, repeat=3)
    assert not again.rebuilt and again.indexed == 0

    timings["search_fts_selective"], fts_selective = timed(
        lambda: service.query(CatalogFilter(text="სატესტო ტექსტი ALI-1")), repeat=10
    )
    timings["search_fts_broad_10000_hits"], fts_broad = timed(
        lambda: service.query(CatalogFilter(text="სატესტო")), repeat=5
    )
    timings["search_fts_by_id"], by_id = timed(
        lambda: service.query(CatalogFilter(text="VENOUS-0777")), repeat=10
    )
    timings["search_like_short_term"], like_short = timed(
        lambda: service.query(CatalogFilter(text="ბა")), repeat=5
    )
    timings["search_fts_plus_filters"], filtered = timed(
        lambda: service.query(
            CatalogFilter(text="სატესტო", subject_code="VENOUS", status=ReviewStatus.PENDING_REVIEW)
        ),
        repeat=5,
    )
    timings["search_result_page_with_previews"], preview_rows = timed(
        lambda: service.rows(fts_broad.ids[:PAGE], fts_broad.terms), repeat=5
    )

    assert (
        fts_selective.mode is SearchMode.FTS
        and fts_broad.mode is SearchMode.FTS
        and by_id.mode is SearchMode.FTS
    )
    assert like_short.mode is SearchMode.LIKE and like_first.mode is SearchMode.LIKE
    assert len(fts_selective) == len(like_first) == 111 and [
        r.uid for r in by_id.ids and service.rows(by_id.ids)
    ] == ["VENOUS-0777"]
    assert len(fts_broad) == 10_000 and 0 < len(filtered) < 10_000
    assert all(row.preview.spans for row in preview_rows)
    assert fts_selective.ids == like_first.ids  # identical answers from both back ends

    for key, value in timings.items():
        record_property(key, round(value, 5))
    print("\nPERF-SERVICE " + "  ".join(f"{k}={v * 1000:.2f}ms" for k, v in timings.items()))

    assert timings["catalog_load_all_ids+first_page"] < 0.25
    assert timings["subject_switch_worst"] < 0.25
    assert timings["status_filter"] < 0.25 and timings["sort_by_status"] < 0.4
    assert timings["open_detail_median"] < 0.02
    assert timings["search_like_first_scan"] < 1.0 and timings["search_like_short_term"] < 1.0
    assert timings["index_build_10k"] < 8.0
    assert timings["search_fts_selective"] < 0.1 and timings["search_fts_by_id"] < 0.1
    assert timings["search_fts_broad_10000_hits"] < 0.5 and timings["search_fts_plus_filters"] < 0.25
    assert timings["search_result_page_with_previews"] < 0.25


def s_target(subject) -> int:
    return subject.target_count


def test_queries_use_indexes_and_no_per_row_lookups(bank: AppPaths) -> None:
    """The plans for the hot queries scan indexes, never the whole of ``question_revisions`` per row."""
    from vascqbank_desktop.db import catalog_repository as repo
    from vascqbank_desktop.db.connection import open_database

    conn = open_database(bank.database_file)
    conn.execute("PRAGMA query_only = ON")
    try:

        def plan(sql: str, params=()) -> str:
            return " | ".join(r[3] for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))

        by_subject = plan(
            "SELECT q.id FROM questions q JOIN subjects s ON s.code = q.subject_code "
            "WHERE q.subject_code = ? ORDER BY s.sort_order, q.source_number",
            ("ALI",),
        )
        assert (
            "USING COVERING INDEX" in by_subject and "TEMP B-TREE" not in by_subject
        )  # ordered by the index
        page = plan(
            "SELECT q.id, q.question_uid, substr(r.stem, 1, 600) FROM questions q "
            "JOIN question_revisions r ON r.id = q.current_revision_id WHERE q.id IN (1,2,3)"
        )
        assert page.count("INTEGER PRIMARY KEY") == 2 and "SCAN" not in page  # primary-key lookups only
        detail = plan(repo._DETAIL_SQL.format(where="q.id = ?"), (1,))
        assert "SCAN" not in detail
        uid = plan(repo._DETAIL_SQL.format(where="q.question_uid = ?"), ("ALI-0001",))
        assert "SCAN" not in uid and "sqlite_autoindex_questions_1" in uid  # the UNIQUE index on question_uid
    finally:
        conn.close()


def test_a_page_of_rows_is_one_query_not_one_per_row(bank: AppPaths) -> None:
    service = CatalogService(bank)
    ids = service.query(CatalogFilter(subject_code="VENOUS")).ids[:PAGE]
    statements: list[str] = []
    from vascqbank_desktop.db.connection import open_database

    conn = open_database(bank.database_file)
    conn.execute("PRAGMA query_only = ON")
    conn.set_trace_callback(statements.append)
    try:
        from vascqbank_desktop.db import catalog_repository as repo

        statements.clear()
        rows = repo.fetch_summaries(conn, ids, ())
    finally:
        conn.close()
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert len(rows) == PAGE and len(selects) == 1
    assert not any("question_revisions" in s and "revision_no" in s for s in statements)  # no history is read


def test_gui_stays_fast_and_memory_stays_flat_at_ten_thousand(
    bank: AppPaths, qtbot, styled_qapp, record_property
) -> None:
    from PySide6.QtCore import QTimer

    from vascqbank_desktop.services.question_stats import bank_counts
    from vascqbank_desktop.ui.catalog_view import CatalogView
    from vascqbank_desktop.ui.question_list_model import MAX_CACHED_PAGES

    service = CatalogService(bank)
    service.ensure_search_index()
    timings: dict[str, float] = {}

    gc.collect()
    rss_before = rss_mb()
    started = time.perf_counter()
    view = CatalogView(service, SUBJECTS)
    qtbot.addWidget(view)
    view.resize(1200, 800)
    view.show()
    view.set_counts(bank_counts(bank))
    qtbot.wait(20)
    timings["window_catalog_ready"] = time.perf_counter() - started
    assert view.model.rowCount() == 10_000

    timings["switch_subject"], _ = timed(lambda: view.set_subject("THORACIC_AORTA"))
    assert view.model.rowCount() == 700
    timings["switch_to_all"], _ = timed(lambda: view.set_subject(None))
    timings["open_question"], _ = timed(lambda: view.open_question(view.model.question_id_at(4321)), repeat=5)
    timings["jump_to_far_question"], _ = timed(lambda: view.go_to("PERIOPERATIVE-0599"))
    assert view.current_uid == "PERIOPERATIVE-0599"
    view.go_to("VENOUS-0100")  # mid-list, so every "next" really moves
    visited: list[str] = []

    def step() -> None:
        view.next_question()
        visited.append(view.current_uid)

    timings["next_question"], _ = timed(step, repeat=5)
    assert visited == [f"VENOUS-{n:04d}" for n in range(101, 106)]  # five distinct, consecutive questions

    # the window must keep processing events while a search runs
    ticks: list[int] = []
    timer = QTimer()
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start(5)
    started = time.perf_counter()
    view.set_search_text("სატესტო ტექსტი VENOUS-1")
    view._commit_search()
    qtbot.waitUntil(lambda: not view.is_busy(), timeout=20_000)
    timings["search_until_list_updated"] = time.perf_counter() - started
    timer.stop()
    assert view.model.results.mode is SearchMode.FTS and view.model.rowCount() == 111
    view.search_edit.clear()
    assert view.model.rowCount() == 10_000

    # memory: page through the whole bank; the page cache must stay bounded
    tracemalloc.start()
    baseline, _peak = tracemalloc.get_traced_memory()
    for row in range(0, view.model.rowCount(), 50):
        view.model.summary_at(row)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    gc.collect()
    rss_after = rss_mb()
    assert view.model.cached_page_count <= MAX_CACHED_PAGES
    paged_kb = max(peak - baseline, current - baseline) / 1024
    timings["python_memory_paging_all_10k_kb"] = paged_kb

    for key, value in timings.items():
        record_property(key, round(value, 5))
    if rss_before is not None and rss_after is not None:
        record_property("rss_before_mb", round(rss_before, 1))
        record_property("rss_after_mb", round(rss_after, 1))
    shown = "  ".join(
        f"{k}={v * 1000:.1f}ms" if not k.endswith("_kb") else f"{k}={v:.0f}KB" for k, v in timings.items()
    )
    rss = (
        f"  rss={rss_before:.0f}->{rss_after:.0f}MB"
        if rss_before is not None and rss_after is not None
        else ""
    )
    print(f"\nPERF-GUI {shown}{rss}  event_loop_ticks_during_search={len(ticks)}")

    assert timings["window_catalog_ready"] < 2.0
    assert timings["switch_subject"] < 0.3 and timings["switch_to_all"] < 0.3
    assert timings["open_question"] < 0.1 and timings["next_question"] < 0.1
    assert timings["jump_to_far_question"] < 0.5
    assert timings["search_until_list_updated"] < 2.0
    assert timings["python_memory_paging_all_10k_kb"] < 20_000  # the bounded cache never holds 10,000 rows
    if rss_before is not None and rss_after is not None:
        assert rss_after - rss_before < 250  # whole window + 10,000-id list + page cache


def test_database_file_sizes(bank: AppPaths, record_property) -> None:
    service = CatalogService(bank)
    service.ensure_search_index()
    database_mb = bank.database_file.stat().st_size / 1_048_576
    index_mb = bank.search_index_file.stat().st_size / 1_048_576
    record_property("database_mb", round(database_mb, 1))
    record_property("search_index_mb", round(index_mb, 1))
    print(f"\nPERF-SIZE database={database_mb:.1f}MB search_index={index_mb:.1f}MB (derived, rebuildable)")
    assert index_mb < 3 * database_mb  # the derived index never dwarfs the data it serves
    assert os.path.getsize(bank.database_file) > 0
