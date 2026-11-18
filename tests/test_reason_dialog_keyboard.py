"""Phase 9b (D19): a rejection reason / reopening note can be confirmed with the keyboard alone.

Before, Tab inside the reason box inserted a tab character (and the confirm button was not the
default), so a keyboard-only reviewer could type the reason but never reach "უარყოფა".
"""

from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402

from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.workflow_widgets import ReasonDialog  # noqa: E402

pytestmark = pytest.mark.gui


def _dialog(qtbot) -> ReasonDialog:
    dialog = ReasonDialog(
        strings.WF_REJECT_TITLE, strings.WF_REJECT_INTRO, confirm_text=strings.WF_REJECT_CONFIRM,
        required=True, destructive=True,
    )  # fmt: skip
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.activateWindow()
    qtbot.waitUntil(dialog.isActiveWindow, timeout=5000)  # shortcuts are window-scoped, as in use
    dialog.edit.setFocus()
    return dialog


def test_tab_leaves_the_reason_and_space_confirms(qtbot, styled_qapp) -> None:
    dialog = _dialog(qtbot)
    dialog.edit.insertPlainText("ტერმინი არასწორია")  # QTest.keyClicks aborts on non-Latin-1 text
    qtbot.keyClick(dialog.edit, Qt.Key.Key_Tab)
    assert "\t" not in dialog.edit.toPlainText()
    for _ in range(3):
        if dialog.focusWidget() is dialog.confirm_button:
            break
        qtbot.keyClick(dialog.focusWidget() or dialog, Qt.Key.Key_Tab)
    assert dialog.focusWidget() is dialog.confirm_button
    qtbot.keyClick(dialog.confirm_button, Qt.Key.Key_Space)
    assert dialog.result() == ReasonDialog.DialogCode.Accepted and dialog.text() == "ტერმინი არასწორია"


def test_ctrl_enter_confirms_from_inside_the_text(qtbot, styled_qapp) -> None:
    dialog = _dialog(qtbot)
    dialog.edit.insertPlainText("მიზეზი")
    qtbot.keyClick(dialog.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert dialog.result() == ReasonDialog.DialogCode.Accepted and dialog.text() == "მიზეზი"


def test_an_empty_required_reason_is_still_refused_from_the_keyboard(qtbot, styled_qapp) -> None:
    dialog = _dialog(qtbot)
    qtbot.keyClick(dialog.edit, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert dialog.isVisible() and dialog.error_label.isVisibleTo(dialog)
    assert dialog.error_label.text() == strings.WF_REASON_REQUIRED
