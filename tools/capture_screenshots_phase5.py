"""Render the editorial workflow with SYNTHETIC data and save PNG screenshots.

    python tools/capture_screenshots_phase5.py OUTPUT_DIR [--questions 10000]

Approve, approve-and-next, reject, reopen and restore on the same throw-away synthetic bank as
the other screenshot tools (artificial filler text, no real medical content) in a temporary
folder; nothing else is touched. Modal dialogs are captured while they are open by a timer
that runs inside their event loop.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from bank import approve  # noqa: E402
from capture_screenshots import build_data, give_statuses, pump  # noqa: E402
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from vascqbank_desktop import resources  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.draft_service import DraftService  # noqa: E402
from vascqbank_desktop.services.edit_service import EditService  # noqa: E402
from vascqbank_desktop.services.settings_service import (  # noqa: E402
    ReviewerProfile,
    SavedSession,
    SettingsStore,
)
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.theme import apply_theme  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--questions", type=int, default=10_000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vqb-shots5-") as tmp:
        paths = build_data(Path(tmp), args.questions)
        give_statuses(paths)
        store = SettingsStore(paths.settings_file)
        profile = ReviewerProfile(store)
        profile.set_name("რამაზი აფხაძე")
        store.set_session(SavedSession("ALI", None, "ALI-0021"))
        approve(paths, "ALI-0012")  # an approved question to reject and reopen
        other = ReviewerProfile(SettingsStore(Path(tmp) / "other.json"))
        other.set_name("დავით ფარღალავა")

        app = QApplication.instance() or QApplication(sys.argv[:1])
        register_bundled_fonts()
        app.setFont(build_ui_font())
        apply_theme(app)
        icon = QIcon(str(resources.resource_path("icons", "app.svg")))
        window = MainWindow(
            initialize_storage(paths), SUBJECTS, app_icon=icon, settings_store=store, autosave_delay_ms=80
        )
        window.resize(1360, 980)
        window.show()
        window.wait_for_background_work()
        view = window.catalog_view
        workflow = view.workflow
        assert workflow is not None

        def shot(name: str) -> None:
            pump(300)
            path = args.output / name
            window.grab().save(str(path))
            print("saved", path)

        def while_modal(name: str, act) -> None:  # noqa: ANN001
            """Capture the modal dialog that is about to open, then let *act* answer it."""

            def capture() -> None:
                dialog = QApplication.activeModalWidget()
                assert dialog is not None
                act(dialog)
                pump(150)
                path = args.output / name
                dialog.grab().save(str(path))
                print("saved", path)
                close = getattr(dialog, "_answer", None)
                if close is not None:
                    close()

            QTimer.singleShot(400, capture)

        # 1. a pending question: the workflow bar
        view.set_status(ReviewStatus.PENDING_REVIEW)
        pump(300)
        view.go_to("ALI-0021")
        shot("16-workflow-bar-pending.png")

        # 2. approve: the status, the list row and the success message update at once
        workflow.approve()
        shot("17-approved.png")

        # 3. approve and next inside a filtered list: it continues with the question that follows
        view.go_to("ALI-0030")
        pump(100)
        workflow.approve_and_next()
        shot("18-approve-and-next.png")

        # 4. rejecting an approved question needs a reason
        view.set_status(None)
        pump(300)
        view.go_to("ALI-0012")
        pump(100)

        def reject_dialog(dialog) -> None:  # noqa: ANN001
            dialog.edit.setPlainText("პასუხი გ არასწორია: მითითებული მაჩვენებელი სხვა ტექსტშია.")
            dialog._answer = dialog.confirm_button.click

        while_modal("19-reject-dialog.png", reject_dialog)
        workflow.reject()
        shot("20-rejected.png")

        # 5. reopening
        def reopen_dialog(dialog) -> None:  # noqa: ANN001
            dialog.edit.setPlainText("ახალი ლიტერატურა გადასახედია")
            dialog._answer = dialog.confirm_button.click

        while_modal("21-reopen-dialog.png", reopen_dialog)
        workflow.reopen()
        shot("22-reopened.png")

        # 6. restoring an earlier revision (with a draft present: the reviewer must decide)
        view.go_to("ALI-0005")
        pump(100)
        detail = window.catalog_service.detail_by_uid("ALI-0005")
        editor_service = EditService(paths, other)
        first = detail.content
        editor_service.save(
            detail.id,
            detail.lock_version,
            type(first)(
                first.stem + " (დაზუსტებული ფორმულირება)", first.options, first.correct, first.explanation
            ),
            "ტერმინოლოგიის დაზუსტება",
        )
        detail = window.catalog_service.detail_by_uid("ALI-0005")
        editor_service.save(
            detail.id,
            detail.lock_version,
            type(first)(
                detail.content.stem,
                detail.content.options,
                detail.content.correct,
                "სრულიად ახალი განმარტება.",
            ),
            "განმარტების გადაწერა",
        )
        approve(paths, "ALI-0005")
        view.reload_current()
        detail = window.catalog_service.detail_by_uid("ALI-0005")
        DraftService(paths).save_draft(
            detail.id,
            detail.revision_id,
            detail.lock_version,
            type(first)("ჩემი დაუსრულებელი მონახაზი", first.options, first.correct, first.explanation),
            "რამაზი აფხაძე",
        )
        view.reload_current()
        view.show_history()
        pump(200)
        view.editing.history.select_revision_number(1)
        shot("23-history-restore.png")

        def restore_dialog(dialog) -> None:  # noqa: ANN001
            assert isinstance(dialog, QMessageBox)
            keep = next(b for b in dialog.buttons() if b.text().startswith("დაბრუნება"))
            dialog._answer = keep.click

        while_modal("24-restore-dialog.png", restore_dialog)
        view.editing.history.restore_button.click()
        view.editing.history.select_revision_number(4)
        shot("25-restored.png")

        window.close()
        window.wait_for_background_work()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
