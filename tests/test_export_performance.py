"""Exporting 10,000 synthetic approved questions: time, size, memory, verification.

Budgets are generous (several times the measured values); measured numbers are printed with
``-s`` and recorded in ARCHITECTURE.md §22. The bulk approval writes straight into a *test*
database (the application has no bulk approval).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest
from synthetic import write_full_bank

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.subjects import SUBJECTS
from vascqbank_desktop.importexport.docx_export import DocxEdition, DocxMeta, SubjectBlock, build_docx
from vascqbank_desktop.services import import_service as svc
from vascqbank_desktop.services.approved_service import ApprovedQuestionsService
from vascqbank_desktop.services.export_service import ExportFormat, ExportRequest, ExportService
from vascqbank_desktop.services.storage import initialize_storage

# ``resource`` (peak RSS) exists only on POSIX; on Windows this benchmark is reported as skipped
# instead of breaking collection of the whole suite.
resource = pytest.importorskip("resource")

pytestmark = pytest.mark.perf

ALL = tuple(s.code for s in SUBJECTS)


def peak_rss_mb() -> float:
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024) if sys.platform == "darwin" else peak / 1024


@pytest.fixture(scope="module")
def approved_bank(tmp_path_factory) -> AppPaths:
    root = tmp_path_factory.mktemp("export-perf")
    source = root / "synthetic-bank"
    assert write_full_bank(source) == 10_000
    paths = AppPaths(data_dir=root / "data")
    initialize_storage(paths)
    report = svc.dry_run(paths, [source])
    assert len(svc.commit_import(paths, report).created) == 10_000
    conn = open_database(paths.database_file)
    try:  # test set-up only: every question approved, with its APPROVE event
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE questions SET status = 'APPROVED', approved_revision_id = current_revision_id, "
            "approved_at = '2026-11-18T00:00:00+00:00', lock_version = 1"
        )
        conn.execute(
            "INSERT INTO review_events (question_id, action, from_status, to_status, revision_id, actor, "
            "created_at) SELECT id, 'APPROVE', 'PENDING_REVIEW', 'APPROVED', current_revision_id, 'ტესტი', "
            "'2026-11-18T00:00:00+00:00' FROM questions"
        )
        conn.execute("COMMIT")
    finally:
        conn.close()
    return paths


def sizes(folder: Path, pattern: str) -> int:
    return sum(p.stat().st_size for p in folder.rglob(pattern))


def test_txt_export_of_10000_questions(approved_bank: AppPaths, tmp_path: Path) -> None:
    started = time.perf_counter()
    result = ExportService(approved_bank).run(
        ExportRequest(subjects=ALL, formats=frozenset({ExportFormat.TXT}), destination=tmp_path)
    )
    total = time.perf_counter() - started
    t = result.timings
    print(
        f"\nTXT 10,000: total {total:.2f} s (snapshot {t['snapshot']:.2f}, write {t['txt']:.2f}, "
        f"parse-back verification {t['verify']:.2f}, manifest {t['manifest']:.2f}); "
        f"files {sizes(result.folder, '*.txt') / 1e6:.1f} MB, manifest "
        f"{(result.folder / 'export-manifest.json').stat().st_size / 1e6:.1f} MB"
    )
    assert result.approved == 10_000 and len(result.files) == 25
    assert total < 60


def test_one_word_document_with_all_10000_questions(approved_bank: AppPaths, tmp_path: Path) -> None:
    snapshot = ApprovedQuestionsService(approved_bank).snapshot()
    blocks: dict[str, list] = {}
    for q in snapshot.questions:
        blocks.setdefault(q.subject_code, []).append(q)
    chosen = [SubjectBlock(s.code, s.name_ka, blocks[s.code]) for s in SUBJECTS if s.code in blocks]
    meta = DocxMeta("perf", "2026-11-18 00:00", snapshot.fingerprint, "0.6.0")
    rss_before = peak_rss_mb()
    started = time.perf_counter()
    document = build_docx(chosen, DocxEdition.REVIEW, meta)
    built = time.perf_counter()
    path = tmp_path / "all.docx"
    document.save(str(path))
    saved = time.perf_counter()
    del document
    print(
        f"\nWord, review edition, 10,000 questions in one document: build {built - started:.1f} s, "
        f"save (ZIP) {saved - built:.1f} s, file {path.stat().st_size / 1e6:.1f} MB, "
        f"peak RSS {rss_before:.0f} → {peak_rss_mb():.0f} MB"
    )
    assert built - started < 120 and saved - built < 60


def test_complete_export_all_editions_and_txt(approved_bank: AppPaths, tmp_path: Path) -> None:
    started = time.perf_counter()
    result = ExportService(approved_bank).run(
        ExportRequest(
            subjects=ALL,
            formats=frozenset({ExportFormat.TXT, ExportFormat.DOCX}),
            destination=tmp_path,
            editions=tuple(DocxEdition),
            per_subject_docx=False,
            combined_docx=True,
        )
    )
    total = time.perf_counter() - started
    t = result.timings
    docx = {f.path: f.size for f in result.files if f.kind == "DOCX"}
    print(
        f"\nComplete export (3 Word editions of 10,000 + TXT): total {total:.1f} s — Word {t['docx']:.1f} s, "
        f"verification {t['verify']:.1f} s, TXT {t['txt']:.1f} s; sizes "
        + ", ".join(f"{Path(k).name} {v / 1e6:.1f} MB" for k, v in docx.items())
        + f"; peak RSS {peak_rss_mb():.0f} MB"
    )
    assert result.approved == 10_000 and len(docx) == 3
    assert total < 600


@pytest.mark.gui
def test_the_window_stays_responsive_during_a_10000_question_export(
    approved_bank: AppPaths, tmp_path: Path, qtbot, styled_qapp
) -> None:
    from PySide6.QtCore import QEventLoop, QTimer

    from vascqbank_desktop.ui.export_view import ExportView

    view = ExportView(ExportService(approved_bank), SUBJECTS, preview_delay_ms=10)
    qtbot.addWidget(view)
    view.show()
    view.destination_edit.setText(str(tmp_path))
    view.combined_check.setChecked(True)
    view.per_subject_check.setChecked(False)

    def pump(ms: int) -> None:
        loop = QEventLoop()
        QTimer.singleShot(ms, loop.quit)
        loop.exec()

    deadline = time.monotonic() + 60
    while view.plan is None:
        assert time.monotonic() < deadline
        pump(20)
    ticks: list[float] = []
    timer = QTimer(view)
    timer.setInterval(20)
    timer.timeout.connect(lambda: ticks.append(time.monotonic()))
    timer.start()
    started = time.monotonic()
    assert view.start_export()
    while view.is_running():
        assert time.monotonic() - started < 300
        pump(50)
    timer.stop()
    gaps = sorted(b - a for a, b in zip(ticks, ticks[1:], strict=False))
    worst, p95 = gaps[-1] * 1000, gaps[int(len(gaps) * 0.95)] * 1000
    print(
        f"\nGUI during a 10,000-question Word export ({time.monotonic() - started:.1f} s): "
        f"event-loop gap p95 {p95:.0f} ms, worst {worst:.0f} ms"
    )
    view.shutdown()  # as the main window does on close: no preview left running
    assert view.last_result is not None and view.last_result.approved == 10_000
    assert worst < 1500
