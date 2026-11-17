"""Render the editor, draft recovery and history with SYNTHETIC data and save PNG screenshots.

    python tools/capture_screenshots_phase4.py OUTPUT_DIR [--questions 10000]

Uses the same throw-away synthetic bank as ``capture_screenshots.py`` (artificial filler text,
no real medical content) in a temporary folder; nothing else is touched.
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
from capture_screenshots import build_data, give_statuses, pump, wait_until  # noqa: E402
from PySide6.QtGui import QIcon  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vascqbank_desktop import resources  # noqa: E402
from vascqbank_desktop.domain.answers import AnswerLetter  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.edit_service import EditService  # noqa: E402
from vascqbank_desktop.services.settings_service import (  # noqa: E402
    ReviewerProfile,
    SavedSession,
    SettingsStore,
)
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui.fonts import build_ui_font, register_bundled_fonts  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.question_editor_view import EditorState  # noqa: E402
from vascqbank_desktop.ui.theme import apply_theme  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    parser.add_argument("--questions", type=int, default=10_000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="vqb-shots4-") as tmp:
        paths = build_data(Path(tmp), args.questions)
        give_statuses(paths)
        store = SettingsStore(paths.settings_file)
        profile = ReviewerProfile(store)
        profile.set_name("რამაზი აფხაძე")
        store.set_session(SavedSession("ALI", None, "ALI-0007"))
        approve(paths, "ALI-0012")  # an approved question for the revocation warning
        # a history: a second reviewer edited ALI-0005 twice
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
                AnswerLetter.A,
                detail.content.explanation + " დამატებითი წინადადება.",
            ),
            "სწორი პასუხის შესწორება",
        )

        def shot(name: str) -> None:
            pump(250)
            path = args.output / name
            window.grab().save(str(path))
            print("saved", path)

        # 1. editing with unsaved text, then the confirmed draft state
        view.go_to("ALI-0007")
        pump(80)
        window.catalog_view.start_editing()
        editor = view.editing.editor
        editor.stem_edit.setPlainText(
            editor.stem_edit.toPlainText() + "\n\nაქ დაემატა მეორე აბზაცი რეცენზენტის მიერ."
        )
        editor.select_correct(3)
        editor.note_edit.setText("გაუმჯობესებული ფორმულირება")
        shot("07-editor-typing.png")
        wait_until(lambda: editor.state is EditorState.DRAFT_SAVED)
        shot("08-editor-draft-saved.png")

        # 2. validation problems
        editor.option_rows[1].edit.setPlainText("")
        editor.explanation_edit.setPlainText("   ")
        editor.save()
        shot("09-editor-validation.png")
        editor.option_rows[1].edit.setPlainText("ბ ვარიანტი — შევსებულია")
        editor.explanation_edit.setPlainText("ახალი განმარტება.")

        # 3. saved
        editor.save()
        shot("10-editor-saved.png")

        # 4. an approved question: the warning
        view.go_to("ALI-0012")
        pump(80)
        window.catalog_view.start_editing()
        shot("11-editor-approved-warning.png")
        editor.stem_edit.setPlainText(editor.stem_edit.toPlainText() + " (შესწორება)")
        wait_until(lambda: editor.state is EditorState.DRAFT_SAVED)
        view.editing.leave_for_navigation()

        # 5. a conflict
        view.go_to("ALI-0008")
        pump(80)
        window.catalog_view.start_editing()
        stale = window.catalog_service.detail_by_uid("ALI-0008")
        editor.stem_edit.setPlainText("ჩემი ვერსია — ტექსტი რეცენზენტ რამაზის მიერ")
        editor.flush_draft()
        fresh = EditService(paths, other)
        fresh.save(
            stale.id,
            stale.lock_version,
            type(stale.content)(
                "ტექსტი სხვა ფანჯრიდან",
                stale.content.options,
                stale.content.correct,
                stale.content.explanation,
            ),
        )
        editor.confirm_revoke_approval = lambda _n: True
        editor.save()
        shot("12-editor-conflict.png")
        editor._keep_draft_and_leave()

        # 6. recovery bar + banner + list marker
        view.go_to("ALI-0008")
        pump(100)
        shot("13-draft-recovery-bar.png")

        # 7. the draft list
        dialog = view.editing.open_draft_list()
        pump(200)
        dialog.grab().save(str(args.output / "14-draft-list.png"))
        print("saved", args.output / "14-draft-list.png")
        dialog.accept()

        # 8. history
        view.go_to("ALI-0005")
        pump(80)
        view.show_history()
        pump(200)
        view.editing.history.select_revision_number(1)
        shot("15-history-compare.png")

        window.close()
        window.wait_for_background_work()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
