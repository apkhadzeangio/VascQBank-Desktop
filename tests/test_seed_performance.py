"""Seed build and first launch with 10,000 synthetic questions: time and size.

Budgets are generous; the measured numbers are printed with ``-s`` (ARCHITECTURE.md §24.8).
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from seedbank import full_sources

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.domain.subjects import SUBJECTS
from vascqbank_desktop.services.catalog_service import CatalogService
from vascqbank_desktop.services.seed_builder import build_seed
from vascqbank_desktop.services.seed_service import SeedKind, verify_seed
from vascqbank_desktop.services.startup import Outcome, prepare_storage

pytestmark = pytest.mark.perf


@pytest.fixture(scope="module")
def built(tmp_path_factory) -> tuple[Path, dict[str, float]]:
    root = tmp_path_factory.mktemp("seed-perf")
    started = time.perf_counter()
    result = build_seed(full_sources(root / "src"), root / "seed", kind=SeedKind.PRODUCTION,
                        seed_version="synthetic-perf")  # fmt: skip
    return result.directory, {"build": time.perf_counter() - started, **result.timings}


def test_seed_build_and_verification(built) -> None:
    seed, timings = built
    started = time.perf_counter()
    verified = verify_seed(seed, require_production=True)
    verify_s = time.perf_counter() - started
    size = (seed / "vascqbank-seed.sqlite3").stat().st_size / 1e6
    print(
        f"\nseed build of 10,000: {timings['build']:.2f} s (dry run {timings['dry_run']:.2f}, import "
        f"{timings['import']:.2f}, compact {timings['compact']:.2f}, checks {timings['verify']:.2f}); "
        f"verification {verify_s:.2f} s; seed {size:.1f} MB"
    )
    assert verified.manifest.question_count == 10_000
    assert timings["build"] < 60 and verify_s < 10


def test_first_launch_restart_and_search_index(built, tmp_path: Path, qtbot, styled_qapp) -> None:
    seed, _ = built
    paths = AppPaths(data_dir=tmp_path / "data")
    started = time.perf_counter()
    first = prepare_storage(paths, seed)
    first_s = time.perf_counter() - started
    assert first.outcome is Outcome.READY and first.seed_install is not None
    started = time.perf_counter()
    again = prepare_storage(paths, seed)
    again_s = time.perf_counter() - started
    assert again.seed_install is None
    started = time.perf_counter()
    index = CatalogService(paths).ensure_search_index()
    index_s = time.perf_counter() - started

    from vascqbank_desktop.ui.main_window import MainWindow

    started = time.perf_counter()
    window = MainWindow(again.status, SUBJECTS)
    qtbot.addWidget(window)
    window.show()
    window_s = time.perf_counter() - started
    window.wait_for_background_work()
    size = paths.database_file.stat().st_size / 1e6
    print(
        f"\nfirst launch (verify + copy + provenance + publish + open): {first_s:.2f} s "
        f"(install {first.seed_install.duration_s:.2f}); later start-up (storage): {again_s * 1000:.0f} ms; "
        f"main window with 10,000 questions: {window_s:.2f} s; search index build: {index_s:.2f} s "
        f"(available {index.available}); working database {size:.1f} MB"
    )
    assert CatalogService(paths).count() == 10_000
    assert first_s < 30 and again_s < 2 and window_s < 10
