"""Render the Phase 8 screens with SYNTHETIC data at several window sizes and save PNG screenshots.

    python tools/capture_screenshots_phase8.py OUTPUT_DIR [--questions 10000] [--scale 1.0]

Uses the throw-away synthetic bank of the tests (artificial filler text, no real medical content)
in a temporary folder; a few questions get decisions and a draft (test set-up). Captures the catalog
at 1280×720, 1366×768, 1440×900 and 1920×1080, the home page, the backup page, the restore dialog,
the shortcut list and the comparison window. ``--scale 1.5`` renders at 150 % (high-DPI check).
Rendered on Qt's offscreen platform: real window managers (Windows, KDE/CachyOS) are not involved.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_parser = argparse.ArgumentParser()
_parser.add_argument("output", type=Path)
_parser.add_argument("--questions", type=int, default=10_000)
_parser.add_argument("--scale", type=float, default=1.0)
ARGS = _parser.parse_args()
if ARGS.scale != 1.0:
    os.environ["QT_SCALE_FACTOR"] = str(ARGS.scale)

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from bank import approve  # noqa: E402
from capture_screenshots import build_data, pump  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402
from synthetic import write_pair  # noqa: E402

from vascqbank_desktop import resources  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.backup_service import create_manual_backup  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts  # noqa: E402
from vascqbank_desktop.ui.localization import install_translations  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_BACKUPS, PAGE_CATALOG, PAGE_EXPORT, PAGE_HOME  # noqa: E402
from vascqbank_desktop.ui.theme import apply_theme  # noqa: E402

SIZES = [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]


def main() -> int:
    out: Path = ARGS.output
    out.mkdir(parents=True, exist_ok=True)
    suffix = "" if ARGS.scale == 1.0 else f"-scale{int(ARGS.scale * 100)}"
    with tempfile.TemporaryDirectory(prefix="vqb-shots8-") as tmp:
        paths = build_data(Path(tmp), ARGS.questions)
        for uid in ("ALI-0001", "ALI-0002", "VENOUS-0004", "PAD_CLTI-0007"):
            approve(paths, uid)
        store = SettingsStore(paths.settings_file)
        ReviewerProfile(store).set_name("რამაზი აფხაძე")
        create_manual_backup(paths)

        app = QApplication.instance() or QApplication(sys.argv[:1])
        register_bundled_fonts()
        app.setFont(build_ui_font())
        apply_theme(app)
        install_translations(app)
        icon = QIcon(str(resources.resource_path("icons", "app.svg")))
        window = MainWindow(initialize_storage(paths), SUBJECTS, app_icon=icon, settings_store=store)
        window.show()
        window.wait_for_background_work()
        view = window.catalog_view

        def shot(widget, name: str) -> None:  # noqa: ANN001
            pump(300)
            path = out / f"{name}{suffix}.png"
            widget.grab().save(str(path))
            print("saved", path)

        # a draft, so the drafts counts and the "with drafts" filter show something
        window.show_page(PAGE_CATALOG)
        assert view.go_to("ALI-0005") and view.start_editing()
        view.editing.editor.stem_edit.setPlainText(
            view.editing.editor.stem_edit.toPlainText() + " — სინთეზური მონახაზი"
        )
        view.leave_editing()
        view.set_quick_filter(1)
        assert view.go_to("ALI-0003")

        sizes = SIZES if ARGS.scale == 1.0 else [(1280, 720)]
        for width, height in sizes:
            window.resize(width, height)
            pump(400)
            shot(window, f"40-catalog-{width}x{height}")
        window.resize(1280, 720)
        window.show_page(PAGE_HOME)
        shot(window, "41-home-1280x720")
        window.show_page(PAGE_EXPORT)
        pump(800)
        shot(window, "42-export-1280x720")
        if ARGS.scale != 1.0:
            return 0

        window.resize(1366, 768)
        window.show_page(PAGE_BACKUPS)
        pump(1500)
        window.backup_view.table.selectRow(0)
        shot(window, "43-backups-1366x768")

        dialog = window.create_restore_dialog(window.backup_view.selected_entry.path)
        dialog.show()
        for _ in range(100):
            if not dialog.is_checking():
                break
            pump(50)
        dialog.confirm_box.setChecked(True)
        dialog.adjustSize()
        shot(dialog, "44-restore-dialog")
        dialog.reject()

        shortcuts = window.create_shortcuts_dialog()
        shortcuts.resize(760, 720)
        shortcuts.show()
        shot(shortcuts, "45-shortcuts")
        shortcuts.reject()

        proposal = Path(tmp) / "proposed"
        write_pair(proposal, "ALI", range(1, 1006))
        compare = window.create_compare_dialog()
        compare.resize(760, 560)
        compare.show()
        compare.compare(proposal)
        for _ in range(400):
            if not compare.is_busy():
                break
            pump(50)
        shot(compare, "46-compare")
        compare.reject()

        window.close()
        window.wait_for_background_work()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
