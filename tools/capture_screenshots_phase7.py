"""Render the Phase 7 first-launch screens with a SYNTHETIC seed and save PNG screenshots.

    python tools/capture_screenshots_phase7.py OUTPUT_DIR [--questions 10000]

Builds a throw-away DEVELOPMENT seed from the synthetic bank of the tests (artificial filler text, no
real medical content) in a temporary folder, installs it into an empty temporary data folder exactly
as a first launch does, and captures: the catalog right after the first launch (with the
development-seed warning), the home page with the seed row, the recovery message shown when the
database is missing but backups exist, and the message for a damaged seed. Everything is discarded.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from capture_screenshots import pump  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from synthetic import write_full_bank, write_pair  # noqa: E402

from vascqbank_desktop import resources  # noqa: E402
from vascqbank_desktop.app import main as app_main  # noqa: E402
from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.seed_builder import build_seed  # noqa: E402
from vascqbank_desktop.services.seed_service import DATABASE_NAME, MANIFEST_NAME, SeedKind  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.startup import prepare_storage  # noqa: E402
from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_CATALOG, PAGE_HOME  # noqa: E402
from vascqbank_desktop.ui.theme import apply_theme  # noqa: E402


def grab_next_dialog(path: Path) -> None:
    """Save the next modal dialog that opens, then close it (its default, harmless choice)."""

    def take() -> None:
        box = QApplication.activeModalWidget()
        if box is None:
            QTimer.singleShot(100, take)
            return
        box.adjustSize()
        pump(200)
        box.grab().save(str(path))
        print("saved", path)
        box.reject()

    QTimer.singleShot(400, take)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--questions", type=int, default=10_000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vqb-shots7-") as tmp:
        root = Path(tmp)
        source = root / "source"
        if args.questions >= 10_000:
            write_full_bank(source)
        else:
            for subject in SUBJECTS:
                write_pair(source, subject.code, range(1, max(1, args.questions // len(SUBJECTS)) + 1))
        built = build_seed(source, root / "seed", kind=SeedKind.DEVELOPMENT, seed_version="dev-shots")
        seed = built.directory

        app = QApplication.instance() or QApplication(sys.argv[:1])
        register_bundled_fonts()
        app.setFont(build_ui_font())
        apply_theme(app)
        icon = QIcon(str(resources.resource_path("icons", "app.svg")))

        # 1. a first launch from the seed: the catalog is ready, nothing was imported by hand
        paths = AppPaths(data_dir=root / "first-launch")
        result = prepare_storage(paths, seed)
        assert result.status is not None and result.seed_install is not None
        store = SettingsStore(paths.settings_file)
        ReviewerProfile(store).set_name("რამაზი აფხაძე")
        window = MainWindow(result.status, SUBJECTS, app_icon=icon, settings_store=store)
        window.resize(1360, 980)
        window.show()
        window.wait_for_background_work()

        def shot(name: str) -> None:
            pump(300)
            window.grab().save(str(args.output / name))
            print("saved", args.output / name)

        window.show_page(PAGE_CATALOG)
        window.wait_for_background_work()
        shot("31-first-launch-catalog.png")
        window.show_page(PAGE_HOME)
        shot("32-first-launch-home.png")
        window.close()
        window.wait_for_background_work()

        # 2. the working database is gone but a backup and a -wal file remain: nothing is created
        lost = AppPaths(data_dir=root / "lost")
        lost.ensure_directories()
        (lost.backups_dir / "vascqbank-20261117-180000-auto.sqlite3").write_bytes(b"synthetic")
        lost.database_file.with_name(lost.database_file.name + "-wal").write_bytes(b"synthetic")
        grab_next_dialog(args.output / "33-recovery-needed.png")
        code = app_main.startup(lost, seed)
        assert code == app_main.EXIT_RECOVERY_NEEDED and not lost.database_file.exists()

        # 3. a damaged seed is refused before anything is created
        damaged = root / "damaged-seed"
        shutil.copytree(seed, damaged)
        manifest = damaged / MANIFEST_NAME
        os.chmod(manifest, 0o644)
        manifest.write_text(manifest.read_text(encoding="utf-8").replace('"questions": 1', '"questions": 9'))
        os.chmod(damaged / DATABASE_NAME, 0o644)
        with open(damaged / DATABASE_NAME, "r+b") as handle:
            handle.seek(4096)
            handle.write(b"\0" * 64)
        fresh = AppPaths(data_dir=root / "fresh")
        grab_next_dialog(args.output / "34-seed-invalid.png")
        code = app_main.startup(fresh, damaged)
        assert code == app_main.EXIT_SEED_INVALID and not fresh.database_file.exists()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
