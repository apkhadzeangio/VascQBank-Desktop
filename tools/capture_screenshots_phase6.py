"""Render the export page with SYNTHETIC data and save PNG screenshots.

    python tools/capture_screenshots_phase6.py OUTPUT_DIR [--questions 10000]

Uses the throw-away synthetic bank of the other screenshot tools (artificial filler text, no real
medical content) in a temporary folder; a few hundred questions are approved with their APPROVE
events (test set-up only). Exports go to the temporary folder and are discarded.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from capture_screenshots import build_data, pump  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vascqbank_desktop import resources  # noqa: E402
from vascqbank_desktop.db.connection import open_database  # noqa: E402
from vascqbank_desktop.domain.question import QuestionContent  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.edit_service import EditService  # noqa: E402
from vascqbank_desktop.services.review_service import ReviewService  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.sidebar import PAGE_EXPORT  # noqa: E402
from vascqbank_desktop.ui.theme import apply_theme  # noqa: E402


def approve_some(paths) -> None:  # noqa: ANN001
    conn = open_database(paths.database_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE questions SET status = 'APPROVED', approved_revision_id = current_revision_id, "
            "approved_at = '2026-11-18T08:00:00+00:00', lock_version = lock_version + 1 "
            "WHERE status = 'PENDING_REVIEW' AND id % 9 = 0"
        )
        conn.execute(
            "INSERT INTO review_events (question_id, action, from_status, to_status, revision_id, actor, "
            "created_at) SELECT id, 'APPROVE', 'PENDING_REVIEW', 'APPROVED', current_revision_id, "
            "'რამაზი აფხაძე', '2026-11-18T08:00:00+00:00' FROM questions WHERE status = 'APPROVED'"
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--questions", type=int, default=10_000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vqb-shots6-") as tmp:
        paths = build_data(Path(tmp), args.questions)
        approve_some(paths)
        store = SettingsStore(paths.settings_file)
        profile = ReviewerProfile(store)
        profile.set_name("რამაზი აფხაძე")

        app = QApplication.instance() or QApplication(sys.argv[:1])
        register_bundled_fonts()
        app.setFont(build_ui_font())
        apply_theme(app)
        icon = QIcon(str(resources.resource_path("icons", "app.svg")))
        window = MainWindow(initialize_storage(paths), SUBJECTS, app_icon=icon, settings_store=store)
        window.resize(1360, 980)
        window.show()
        window.wait_for_background_work()
        page = window.export_view
        page.destination_edit.setText(str(Path(tmp) / "exports"))

        def settle() -> None:
            deadline = time.monotonic() + 30
            while page.is_previewing() or page.plan is None:
                pump(50)
                if time.monotonic() > deadline:
                    break

        def shot(name: str) -> None:
            pump(250)
            window.grab().save(str(args.output / name))
            print("saved", args.output / name)

        window.show_page(PAGE_EXPORT)
        settle()
        shot("26-export-page.png")

        page.select_subjects(["ALI", "AAA_EVAR", "VENOUS"])
        page.txt_check.setChecked(True)
        page.exam_check.setChecked(True)
        page.key_check.setChecked(True)
        page.combined_check.setChecked(True)
        settle()
        shot("27-export-options.png")

        # an approved question whose text the TXT format cannot carry unchanged
        detail = window.catalog_service.detail_by_uid("ALI-0009")
        c = detail.content
        EditService(paths, profile).save(
            detail.id,
            detail.lock_version,
            QuestionContent("პირველი აბზაცი\n\nმეორე აბზაცი", c.options, c.correct, c.explanation),
        )
        detail = window.catalog_service.detail_by_uid("ALI-0009")
        ReviewService(paths, profile).approve(detail.id, detail.lock_version)
        page.request_preview()
        settle()
        shot("28-export-txt-blocked.png")
        page.txt_check.setChecked(False)
        settle()

        page.start_export()
        deadline = time.monotonic() + 120
        while page.is_running() and page.progress_bar.value() < 30 and time.monotonic() < deadline:
            pump(20)
        shot("29-export-progress.png")
        while page.is_running() and time.monotonic() < deadline + 300:
            pump(50)
        shot("30-export-done.png")

        window.close()
        window.wait_for_background_work()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
