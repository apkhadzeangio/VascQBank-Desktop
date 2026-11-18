"""Phase 9b, campaign I — every dialog on real screen sizes, keyboard-only and in Georgian.

Each dialog the program can show is opened over a maximised main window (synthetic data). Asserted:
it fits the screen, no visible button or input is outside the dialog (or out of reach of a scroll
area) and no button is narrower than its text; Tab reaches every enabled, focusable control; and no
Latin-script word appears that is not a technical identifier.

The offscreen platform's default screen is 800×800, which is not a screen anyone has, and
``QMessageBox`` sizes itself relative to the screen. So every screen configuration runs in its own
child process with an offscreen screen of that logical size and device-pixel ratio:
1280×720 and 1366×768 at 100 %, 1920×1080 at 150 % (1280×720 logical, DPR 1.5) and at 125 %
(1536×864 logical).
Offscreen rendering on this machine — not a real Windows or KDE desktop.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from bank import approve, seed_counts, simulate_edit  # noqa: E402
from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QAbstractScrollArea,
    QApplication,
    QComboBox,
    QDialog,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QScrollArea,
    QTextEdit,
    QWidget,
)
from test_reviewer_ux import latin_words, visible_texts  # noqa: E402

from vascqbank_desktop.app import main as app_main  # noqa: E402
from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.domain.textdiff import compare_contents  # noqa: E402
from vascqbank_desktop.services.backup_service import create_manual_backup  # noqa: E402
from vascqbank_desktop.services.catalog_service import CatalogService  # noqa: E402
from vascqbank_desktop.services.draft_service import DraftService  # noqa: E402
from vascqbank_desktop.services.startup import prepare_storage  # noqa: E402
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.diff_view import CompareDialog as DiffDialog  # noqa: E402
from vascqbank_desktop.ui.draft_widgets import DraftListDialog  # noqa: E402
from vascqbank_desktop.ui.import_dialog import ImportDialog  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.restore_dialog import RestoreDialog  # noqa: E402
from vascqbank_desktop.ui.workflow_widgets import ReasonDialog  # noqa: E402

pytestmark = pytest.mark.gui
CHILD = os.environ.get("VQB_DIALOG_SCREEN")  # set only in the child runs below
SCREEN = tuple(int(x) for x in CHILD.split("x")) if CHILD else (1280, 720)
CONFIGS = {  # name: (logical width, logical height, device-pixel ratio)
    "1280x720-100": (1280, 720, 1.0),
    "1920x1080-150": (1280, 720, 1.5),
    "1920x1080-125": (1536, 864, 1.25),
    "1366x768-100": (1366, 768, 1.0),
}


@pytest.fixture
def window(qtbot, app_paths: AppPaths, styled_qapp) -> MainWindow:

    seed_counts(app_paths, {"ALI": 8, "VENOUS": 3})
    simulate_edit(app_paths, "ALI-0002", stem="შესწორებული — ხელოვნური ტექსტი, საკმაოდ გრძელი ფორმულირებით")
    approve(app_paths, "ALI-0001")
    create_manual_backup(app_paths)
    window = MainWindow(initialize_storage(app_paths), SUBJECTS)
    qtbot.addWidget(window)
    window.resize(*SCREEN)
    window.show()
    window.wait_for_background_work()
    return window


def _detail(paths: AppPaths, uid: str):  # noqa: ANN202
    detail = CatalogService(paths).detail_by_uid(uid)
    assert detail is not None
    return detail


def _draft_list(window: MainWindow, paths: AppPaths) -> QDialog:
    d = _detail(paths, "ALI-0003")
    changed = type(d.content)("მონახაზი — " + d.content.stem, d.content.options, d.content.correct, "x")
    DraftService(paths).save_draft(d.id, d.revision_id, d.lock_version, changed, "რეცენზენტი")
    dialog = DraftListDialog(window)
    dialog.set_drafts(DraftService(paths).list_drafts())
    return dialog


def _diff(window: MainWindow, paths: AppPaths) -> QDialog:
    d = _detail(paths, "ALI-0002")
    first = _detail(paths, "ALI-0003").content
    return DiffDialog("ვერსიების შედარება", compare_contents(first, d.content), window)


def _damaged_box(window: MainWindow, paths: AppPaths) -> QDialog:
    """The Phase 9b damaged-database message, built exactly as ask_damaged builds it."""
    boxes: list[QDialog] = []
    original = QMessageBox.exec

    def capture(box: QMessageBox) -> int:
        boxes.append(box)
        return 0

    damaged = Path(paths.data_dir.parent / "damaged")
    damaged_paths = AppPaths(data_dir=damaged)
    damaged_paths.ensure_directories()
    damaged_paths.database_file.write_bytes(b"")
    create_manual_backup(paths).path.rename(
        damaged_paths.backups_dir / "vascqbank-20260101-000000-000000-manual.sqlite3"
    )
    result = prepare_storage(damaged_paths, None)
    QMessageBox.exec = capture  # type: ignore[method-assign]
    try:
        app_main.ask_damaged(result, damaged)
    finally:
        QMessageBox.exec = original  # type: ignore[method-assign]
    return boxes[0]


def _recovery_box(window: MainWindow, paths: AppPaths) -> QDialog:
    """The start-up message for a missing database (Phase 7/8), built as ask_recovery builds it."""
    boxes: list[QDialog] = []
    original = QMessageBox.exec
    QMessageBox.exec = lambda box: boxes.append(box) or 0  # type: ignore[method-assign]
    try:
        missing = AppPaths(data_dir=paths.data_dir.parent / "missing")
        missing.ensure_directories()
        create_manual_backup(paths).path.rename(
            missing.backups_dir / "vascqbank-20260101-000000-000000-manual.sqlite3"
        )
        app_main.ask_recovery(prepare_storage(missing, None).evidence, missing.data_dir)
    finally:
        QMessageBox.exec = original  # type: ignore[method-assign]
    return boxes[0]


DIALOGS: dict[str, Callable[[MainWindow, AppPaths], QDialog]] = {
    "missing-database": _recovery_box,
    "restore": lambda w, p: RestoreDialog(p, current=w._status, parent=w),
    "restore-missing-database": lambda w, p: RestoreDialog(p, current=None, parent=w),
    "import": lambda w, p: ImportDialog(p, w),
    "compare-with-newer-source": lambda w, p: w.create_compare_dialog(),
    "shortcuts": lambda w, p: w.create_shortcuts_dialog(),
    "reject-reason": lambda w, p: ReasonDialog(
        strings.WF_REJECT_TITLE,
        strings.WF_REJECT_INTRO,
        confirm_text=strings.WF_REJECT_CONFIRM,
        required=True,
        warning=strings.WF_REJECT_APPROVED_WARNING,
        destructive=True,
        parent=w,
    ),  # fmt: skip
    "reopen-note": lambda w, p: ReasonDialog(
        strings.WF_REOPEN_TITLE,
        strings.WF_REOPEN_INTRO_APPROVED,
        confirm_text=strings.WF_REOPEN_CONFIRM,
        required=False,
        parent=w,
    ),  # fmt: skip
    "draft-list": _draft_list,
    "revision-comparison": _diff,
    "damaged-database": _damaged_box,
}


def _inside_or_scrollable(widget: QWidget, dialog: QDialog) -> bool:
    parent = widget.parentWidget()
    while parent is not None and parent is not dialog:
        if isinstance(parent, QScrollArea | QAbstractScrollArea) and parent is not widget:
            return True
        parent = parent.parentWidget()
    top_left = widget.mapTo(dialog, widget.rect().topLeft())
    return dialog.rect().contains(QRect(top_left, widget.size()))


def _controls(dialog: QDialog) -> list[QWidget]:
    kinds = (QAbstractButton, QComboBox, QLineEdit, QPlainTextEdit, QTextEdit)
    return [w for w in dialog.findChildren(QWidget) if isinstance(w, kinds) and w.isVisibleTo(dialog)]


if CHILD:  # collected only inside the screen-configured child processes

    @pytest.mark.parametrize("name", sorted(DIALOGS))
    def test_every_dialog_fits_is_keyboard_reachable_and_georgian(
        qtbot, window: MainWindow, app_paths: AppPaths, name: str
    ) -> None:
        dialog = DIALOGS[name](window, app_paths)
        qtbot.addWidget(dialog)
        dialog.show()
        QApplication.processEvents()
        qtbot.wait(50)

        # fits a 1280x720 (logical) screen; nothing clipped or out of reach
        assert dialog.width() <= SCREEN[0] and dialog.height() <= SCREEN[1], (name, dialog.size())
        for widget in _controls(dialog):
            assert _inside_or_scrollable(widget, dialog), (name, type(widget).__name__, widget.objectName())
            if isinstance(widget, QAbstractButton) and widget.text() and widget.isVisible():
                assert widget.width() >= widget.sizeHint().width() - 2, (name, widget.text())

        # keyboard only: Tab reaches every enabled, tab-focusable control
        focusable = [
            w
            for w in _controls(dialog)
            if w.isEnabled() and w.isVisible() and w.focusPolicy() & Qt.FocusPolicy.TabFocus
        ]
        dialog.activateWindow()
        (focusable[0] if focusable else dialog).setFocus()
        reached: set[int] = set()
        for _ in range(4 * len(focusable) + 10):
            current = QApplication.focusWidget()
            if current is not None:
                reached.add(id(current))
            qtbot.keyClick(current or dialog, Qt.Key.Key_Tab)
        missing = [
            (w.text() if isinstance(w, QAbstractButton) else w.objectName()) or type(w).__name__
            for w in focusable
            if id(w) not in reached
        ]
        assert not missing, (name, missing)

        # Georgian only (technical identifiers allowed), Qt's own buttons included
        texts = [dialog.windowTitle(), *visible_texts(dialog)]
        if isinstance(dialog, QMessageBox):
            texts += [dialog.text(), *(b.text() for b in dialog.buttons())]
        bad = {t: latin_words(t) for t in texts if t and latin_words(t)}
        assert not bad, (name, bad)
        dialog.close()


if not CHILD:

    @pytest.mark.parametrize("config", sorted(CONFIGS))
    def test_every_dialog_on_a_real_screen_size(tmp_path: Path, config: str) -> None:
        width, height, ratio = CONFIGS[config]
        screen = tmp_path / "screen.json"
        screen.write_text(
            json.dumps({"screens": [{"name": "s", "x": 0, "y": 0, "width": width, "height": height,
                                     "logicalDpi": 96, "logicalBaseDpi": 96, "dpr": ratio}]}),
            encoding="utf-8",
        )  # fmt: skip
        env = {
            **os.environ,
            "QT_QPA_PLATFORM": f"offscreen:configfile={screen}",
            "VQB_DIALOG_SCREEN": f"{width}x{height}",
        }
        child = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", __file__, "-k", "fits_is"],
            capture_output=True, text=True, timeout=600, env=env, cwd=Path(__file__).resolve().parent.parent,
        )  # fmt: skip
        assert child.returncode == 0, child.stdout[-5000:] + child.stderr[-2000:]
        assert f"{len(DIALOGS)} passed" in child.stdout, child.stdout[-2000:]
