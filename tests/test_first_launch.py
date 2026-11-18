"""First launch through the real window and the real entry point (temporary data folders only)."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from seedbank import rewrite_manifest, small_seed, writable  # noqa: E402

from vascqbank_desktop.app import main as app_main  # noqa: E402
from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services import backup_service  # noqa: E402
from vascqbank_desktop.services.startup import Outcome, prepare_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_CATALOG  # noqa: E402

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def seed(tmp_path_factory) -> Path:
    return small_seed(tmp_path_factory.mktemp("seed"))


def window_for(qtbot, paths: AppPaths, seed_dir: Path | None) -> MainWindow:
    result = prepare_storage(paths, seed_dir)
    assert result.outcome is Outcome.READY
    window = MainWindow(result.status, SUBJECTS)
    qtbot.addWidget(window)
    window.show()
    return window


def test_with_a_seed_the_catalog_is_ready_without_any_import(
    qtbot, styled_qapp, app_paths: AppPaths, seed: Path
) -> None:
    window = window_for(qtbot, app_paths, seed)
    window.show_page(PAGE_CATALOG)
    qtbot.waitUntil(lambda: not window.catalog_view.is_busy(), timeout=10_000)
    assert window.catalog_view.model.rowCount() == 26
    counts = window.refresh_counts()
    assert counts.total == 26 and counts.total_with(ReviewStatus.PENDING_REVIEW) == 26
    assert window.bank_label.text() == strings.STATUS_BANK.format(total=26)
    assert not window.seed_label.isHidden() and window.seed_label.text() == strings.STATUS_DEV_SEED
    seed_row = window.welcome_view.findChildren(type(window.seed_label))
    assert any(
        strings.SEED_KIND_DEVELOPMENT in label.text() and "dev-1" in label.text() for label in seed_row
    )
    window.reviewer_view.name_combo.setCurrentText("რამაზ აფხაძე")  # the reviewer can start at once
    window.reviewer_view.save()
    assert window.catalog_view.go_to("ALI-0001") and window.catalog_view.workflow.approve()


def test_without_a_seed_the_manual_import_path_is_unchanged(qtbot, styled_qapp, app_paths: AppPaths) -> None:
    window = window_for(qtbot, app_paths, None)
    assert window.refresh_counts().total == 0 and window.seed_label.isHidden()
    labels = [label.text() for label in window.welcome_view.findChildren(type(window.seed_label))]
    assert strings.STORAGE_SEED_NONE in labels  # the empty catalog is explained


def test_the_recovery_dialog_path_creates_nothing_unless_asked(
    styled_qapp, app_paths: AppPaths, seed: Path, monkeypatch
) -> None:
    prepare_storage(app_paths, seed)
    backup_service.create_manual_backup(app_paths)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{app_paths.database_file}{suffix}").unlink(missing_ok=True)
    asked: list[int] = []
    monkeypatch.setattr(
        app_main, "ask_recovery", lambda evidence, data_dir, exit_after_ms=0: asked.append(1) or "exit"
    )
    assert app_main.startup(app_paths, seed) == app_main.EXIT_RECOVERY_NEEDED
    assert asked and not app_paths.database_file.exists()
    monkeypatch.setattr(app_main, "ask_recovery", lambda evidence, data_dir, exit_after_ms=0: "new")
    result = app_main.startup(app_paths, seed)
    assert result.outcome is Outcome.READY and app_paths.database_file.exists()
    assert list(app_paths.backups_dir.glob("vascqbank-*.sqlite3"))  # the backup is still there


def test_the_real_recovery_dialog_defaults_to_exit(styled_qapp, app_paths: AppPaths, seed: Path) -> None:
    prepare_storage(app_paths, seed)
    backup_service.create_manual_backup(app_paths)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{app_paths.database_file}{suffix}").unlink(missing_ok=True)
    assert app_main.startup(app_paths, seed, exit_after_ms=300) == app_main.EXIT_RECOVERY_NEEDED
    assert not app_paths.database_file.exists()


def test_an_invalid_seed_stops_with_a_message_and_creates_nothing(
    styled_qapp, app_paths: AppPaths, seed: Path, tmp_path: Path
) -> None:
    import shutil

    broken = tmp_path / "broken"
    shutil.copytree(seed, broken)
    for p in broken.iterdir():
        writable(p)
    rewrite_manifest(broken, seed_database_id="tampered")
    assert app_main.startup(app_paths, broken, exit_after_ms=300) == app_main.EXIT_SEED_INVALID
    assert not app_paths.database_file.exists()


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "QT_QPA_PLATFORM": os.environ.get("QT_QPA_PLATFORM", "offscreen"),
        "PYTHONIOENCODING": "utf-8",
    }
    env.pop("VASCQBANK_SEED_DIR", None)
    env.pop("VASCQBANK_DATA_DIR", None)
    return subprocess.run([sys.executable, "-m", "vascqbank_desktop", *args], capture_output=True, text=True,
                          encoding="utf-8", env=env, timeout=120)  # fmt: skip


def test_the_entry_point_installs_once_and_reports_provenance(tmp_path: Path, seed: Path) -> None:
    data = tmp_path / "data"
    first = _run("--self-check", "--data-dir", str(data), "--seed-dir", str(seed))
    assert first.returncode == 0, first.stderr
    assert (
        "seed:              dev-1 (DEVELOPMENT" in first.stdout and "created_new:       False" in first.stdout
    )
    database_id = next(line for line in first.stdout.splitlines() if line.startswith("database_id"))
    second = _run("--self-check", "--data-dir", str(data), "--seed-dir", str(seed))
    assert second.returncode == 0 and database_id in second.stdout  # the same database, not reinstalled
    env_run = subprocess.run(
        [sys.executable, "-m", "vascqbank_desktop", "--self-check", "--data-dir", str(tmp_path / "env")],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "VASCQBANK_SEED_DIR": str(seed), "PYTHONIOENCODING": "utf-8"},
    )  # fmt: skip
    assert env_run.returncode == 0 and "dev-1" in env_run.stdout


def test_the_entry_point_refuses_to_replace_a_lost_database(tmp_path: Path, seed: Path) -> None:
    data = tmp_path / "data"
    assert _run("--self-check", "--data-dir", str(data), "--seed-dir", str(seed)).returncode == 0
    paths = AppPaths(data_dir=data)
    backup_service.create_manual_backup(paths)
    for suffix in ("", "-wal", "-shm"):
        Path(f"{paths.database_file}{suffix}").unlink(missing_ok=True)
    lost = _run("--self-check", "--data-dir", str(data), "--seed-dir", str(seed))
    assert lost.returncode == app_main.EXIT_RECOVERY_NEEDED and "RECOVERY NEEDED" in lost.stdout
    assert "BACKUPS: 1" in lost.stdout and not paths.database_file.exists()


def test_the_real_gui_starts_from_the_seed(tmp_path: Path, seed: Path) -> None:
    data = tmp_path / "data"
    result = _run("--data-dir", str(data), "--seed-dir", str(seed), "--exit-after-ms", "1500")
    if result.returncode != 0 and "Qt platform plugin" in result.stderr:
        pytest.skip("Qt platform plugin unavailable")
    assert result.returncode == 0, result.stderr
    assert AppPaths(data_dir=data).database_file.exists()
    log = (data / "logs" / "vascqbank-desktop.log").read_text(encoding="utf-8")
    assert "Installed DEVELOPMENT seed dev-1 (26 questions)" in log and "Traceback" not in log
