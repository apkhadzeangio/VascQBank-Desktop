"""10,000 synthetic questions: parse, validate, import, re-check.

Budgets are deliberately generous so the test is stable on slow machines;
measured timings are printed (run with ``-s``) and recorded as test properties.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from synthetic import write_full_bank

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.services import import_service as svc
from vascqbank_desktop.services.question_stats import bank_counts
from vascqbank_desktop.services.storage import initialize_storage

pytestmark = pytest.mark.perf


def test_ten_thousand_synthetic_questions(tmp_path: Path, app_paths: AppPaths, record_property) -> None:
    source = tmp_path / "synthetic-bank"
    assert write_full_bank(source) == 10_000
    initialize_storage(app_paths)
    timings: dict[str, float] = {}

    t = time.perf_counter()
    report = svc.dry_run(app_paths, [source])
    timings["dry_run_10k"] = time.perf_counter() - t
    counts = report.counts
    assert (counts.total_parsed, counts.valid, counts.eligible, counts.errors) == (10_000, 10_000, 10_000, 0)

    t = time.perf_counter()
    result = svc.commit_import(app_paths, report)
    timings["import_10k_incl_backup"] = time.perf_counter() - t
    assert len(result.created) == 10_000

    t = time.perf_counter()
    counts_by_subject = bank_counts(app_paths)
    timings["subject_counts"] = time.perf_counter() - t
    assert counts_by_subject.total == 10_000

    t = time.perf_counter()
    again = svc.dry_run(app_paths, [source])
    timings["dry_run_against_10k_db"] = time.perf_counter() - t
    assert again.counts.identical_existing == 10_000 and again.counts.eligible == 0

    size_mb = app_paths.database_file.stat().st_size / 1_048_576
    for key, value in timings.items():
        record_property(key, round(value, 3))
    print("\nPERF " + "  ".join(f"{k}={v:.2f}s" for k, v in timings.items()) + f"  db={size_mb:.1f}MB")

    assert timings["dry_run_10k"] < 10
    assert timings["import_10k_incl_backup"] < 15
    assert timings["subject_counts"] < 0.5
    assert timings["dry_run_against_10k_db"] < 10
