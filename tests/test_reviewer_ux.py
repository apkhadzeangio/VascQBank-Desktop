"""Phase 8 reviewer workspace: keyboard workflow, quick filters, navigation, counts, localization,
screen sizes, focus order and accessibility (synthetic data in temporary folders only)."""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PySide6")

from bank import approve, make_question, seed, seed_counts, snapshot  # noqa: E402
from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtGui import QKeySequence, QShortcut  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QAbstractButton,
    QApplication,
    QComboBox,
    QDialogButtonBox,
    QLabel,
    QMessageBox,
    QScrollArea,
    QWidget,
)

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.catalog import CatalogFilter  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services.catalog_service import CatalogService  # noqa: E402
from vascqbank_desktop.services.settings_service import (  # noqa: E402
    ReviewerProfile,
    SavedSession,
    SettingsStore,
)
from vascqbank_desktop.services.storage import initialize_storage  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.main_window import MainWindow  # noqa: E402
from vascqbank_desktop.ui.question_editor_view import EditorState  # noqa: E402
from vascqbank_desktop.ui.restore_dialog import RestoreDialog  # noqa: E402
from vascqbank_desktop.ui.sidebar import (  # noqa: E402
    PAGE_BACKUPS,
    PAGE_CATALOG,
    PAGE_EXPORT,
    PAGE_HOME,
    PAGE_REVIEWER,
)

pytestmark = pytest.mark.gui
WAIT_MS = 10_000
REVIEWER = "რამაზ აფხაძე"
SIZES = [(1280, 720), (1366, 768), (1440, 900), (1920, 1080)]
PAGES = [PAGE_HOME, PAGE_CATALOG, PAGE_BACKUPS, PAGE_REVIEWER, PAGE_EXPORT]


def open_window(qtbot, paths: AppPaths) -> MainWindow:
    window = MainWindow(initialize_storage(paths), SUBJECTS, autosave_delay_ms=60)
    qtbot.addWidget(window)
    window.show()
    window.activateWindow()
    return window


def save_draft_through_the_editor(qtbot, window: MainWindow, uid: str, text: str) -> None:
    view = window.catalog_view
    assert view.go_to(uid) and view.start_editing()
    view.editing.editor.stem_edit.setPlainText(text)
    assert view.leave_editing()


@pytest.fixture
def populated(app_paths: AppPaths, styled_qapp) -> AppPaths:
    seed_counts(app_paths, {"ALI": 10, "VENOUS": 5})
    ReviewerProfile(SettingsStore(app_paths.settings_file)).set_name(REVIEWER)
    return app_paths


@pytest.fixture
def window(qtbot, populated: AppPaths) -> MainWindow:
    window = open_window(qtbot, populated)
    window.show_page(PAGE_CATALOG)
    return window


def listed_uids(window: MainWindow) -> list[str]:
    return [window.catalog_service.locate_id(i).uid for i in window.catalog_view.model.results.ids]


# ------------------------------------------------------------------ quick filters
def test_quick_filters_select_by_status_and_drafts_without_changing_anything(
    qtbot, window: MainWindow, populated: AppPaths
) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0001") and view.workflow.approve()
    assert view.go_to("ALI-0002") and view.workflow.start_review()
    save_draft_through_the_editor(qtbot, window, "VENOUS-0003", "დაუსრულებელი მონახაზი")
    before = snapshot(populated)

    qtbot.keyClick(view, Qt.Key.Key_4, Qt.KeyboardModifier.AltModifier)  # approved
    assert view.quick_filter_index == 3 and view.quick_buttons[3].isChecked()
    assert listed_uids(window) == ["ALI-0001"]
    qtbot.keyClick(view, Qt.Key.Key_3, Qt.KeyboardModifier.AltModifier)  # in review
    assert listed_uids(window) == ["ALI-0002"]
    qtbot.keyClick(view, Qt.Key.Key_6, Qt.KeyboardModifier.AltModifier)  # with drafts
    assert view.current_filter.drafts_only and view.current_filter.status is None
    assert listed_uids(window) == ["VENOUS-0003"]
    view.quick_buttons[1].click()  # pending: drafts flag dropped
    assert not view.current_filter.drafts_only and view.current_filter.status is ReviewStatus.PENDING_REVIEW
    assert len(listed_uids(window)) == 13
    qtbot.keyClick(view, Qt.Key.Key_1, Qt.KeyboardModifier.AltModifier)  # all
    assert len(listed_uids(window)) == 15 and view.quick_buttons[0].isChecked()
    view.status_combo.setCurrentIndex(4)  # the programmatic selector keeps the chips in step
    assert view.quick_buttons[4].isChecked()
    assert snapshot(populated) == before  # filtering never changes a status or anything else


def test_the_drafts_filter_in_the_catalog_service(populated: AppPaths, qtbot, window: MainWindow) -> None:
    save_draft_through_the_editor(qtbot, window, "ALI-0005", "მონახაზი")
    service = CatalogService(populated)
    assert len(service.query(CatalogFilter(drafts_only=True))) == 1
    assert len(service.query(CatalogFilter(subject_code="VENOUS", drafts_only=True))) == 0
    assert len(service.query(CatalogFilter(text="სინთეზური", drafts_only=True))) == 1


# ------------------------------------------------------------------ navigation keeps the place
def test_next_continues_after_a_question_left_the_filter(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    view.set_quick_filter(1)  # pending
    assert view.go_to("ALI-0004") and view.workflow.approve()  # no "next": it drops out of the list
    assert view.current_uid == "ALI-0004" and view.current_question_id not in view.model.results
    view.next_question()
    assert view.current_uid == "ALI-0005"  # not the first pending question of the list
    assert view.go_to("ALI-0007") and view.workflow.approve()
    view.previous_question()
    assert view.current_uid == "ALI-0006"
    assert view.current_filter.status is ReviewStatus.PENDING_REVIEW  # the filter stayed as it was


def test_approve_and_next_keeps_subject_and_filter(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    window.open_subject("ALI")
    view.set_quick_filter(1)
    assert view.go_to("ALI-0002")
    for expected in ("ALI-0003", "ALI-0004", "ALI-0005"):
        qtbot.keyClick(view.list_view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
        assert view.current_uid == expected, view.notice_label.text()
    assert view.current_filter.subject_code == "ALI" and view.quick_filter_index == 1


# ------------------------------------------------------------------ keyboard-only workflow
def test_the_whole_review_cycle_works_with_the_keyboard(
    qtbot, window: MainWindow, populated: AppPaths
) -> None:
    view = window.catalog_view
    window.action_focus_list.trigger()  # Ctrl+L
    assert view.list_view.hasFocus() or QApplication.focusWidget() is view.list_view
    assert view.current_uid == "ALI-0001"
    qtbot.keyClick(view.list_view, Qt.Key.Key_Down)
    assert view.current_uid == "ALI-0002"
    qtbot.keyClick(view, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)  # edit
    editor = view.editing.editor
    assert view.is_editing
    editor.stem_edit.setFocus()
    editor.stem_edit.setPlainText("კლავიატურით შესწორებული ტექსტი")
    qtbot.waitUntil(lambda: editor.state is EditorState.DRAFT_SAVED, timeout=WAIT_MS)
    # an autosaved draft never approves and never changes the status
    assert window.catalog_service.detail_by_uid("ALI-0002").status is ReviewStatus.PENDING_REVIEW
    qtbot.keyClick(editor.stem_edit, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)  # save revision
    qtbot.waitUntil(lambda: editor.state is EditorState.SAVED, timeout=WAIT_MS)
    qtbot.keyClick(editor.stem_edit, Qt.Key.Key_Escape)  # back to reading
    assert not view.is_editing
    detail = window.catalog_service.detail_by_uid("ALI-0002")
    # the first saved revision starts the review (domain/workflow.py); it never approves
    assert detail.status is ReviewStatus.IN_REVIEW and detail.revision_no == 2
    qtbot.keyClick(view.list_view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)  # approve+next
    assert window.catalog_service.detail_by_uid("ALI-0002").status is ReviewStatus.APPROVED
    assert view.current_uid == "ALI-0003"
    qtbot.keyClick(
        view, Qt.Key.Key_I, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    )
    assert window.catalog_service.detail_by_uid("ALI-0003").status is ReviewStatus.IN_REVIEW
    qtbot.keyClick(view, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
    assert view.current_uid == "ALI-0004"


def test_moving_on_while_typing_keeps_the_text_as_a_draft(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0006") and view.start_editing()
    view.editing.editor.stem_edit.setPlainText("ჯერ არ შენახული ტექსტი")
    view.next_question()  # before the autosave timer fired
    assert view.current_uid == "ALI-0007" and not view.is_editing
    assert window.draft_service.count() == 1
    assert window.catalog_service.detail_by_uid("ALI-0006").status is ReviewStatus.PENDING_REVIEW
    assert window.drafts_label.isVisible()
    assert window.refresh_counts().subjects["ALI"].drafts == 1


@pytest.mark.parametrize("page", [PAGE_EXPORT, PAGE_BACKUPS, PAGE_HOME, PAGE_REVIEWER])
def test_switching_pages_while_typing_keeps_the_text(qtbot, window: MainWindow, page: str) -> None:
    view = window.catalog_view
    assert view.go_to("VENOUS-0002") and view.start_editing()
    view.editing.editor.stem_edit.setPlainText(f"ტექსტი გვერდზე {page} გადასვლამდე")
    window.show_page(page)
    assert window.draft_service.count() == 1
    window.show_page(PAGE_CATALOG)
    assert view.is_editing and "გადასვლამდე" in view.editing.editor.stem_edit.toPlainText()


# ------------------------------------------------------------------ shortcuts
def _sequences(window: MainWindow) -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    for action in window.findChildren(type(window.action_quit)):
        for key in action.shortcuts():
            found.append((key.toString(QKeySequence.SequenceFormat.PortableText), action))
    for shortcut in window.findChildren(QShortcut):
        found.append((shortcut.key().toString(QKeySequence.SequenceFormat.PortableText), shortcut))
    return found


def test_shortcuts_do_not_collide(window: MainWindow) -> None:
    seen: dict[str, list[object]] = {}
    for key, owner in _sequences(window):
        seen.setdefault(key, []).append(owner)
    allowed_twice = {"Esc"}  # search field (field-only) and editor (editor-only): never active together
    for key, owners in seen.items():
        if key in allowed_twice:
            continue
        parents = {o.parent() for o in owners if isinstance(o, QShortcut)}
        assert len(owners) == 1 or len(parents) == len(owners), (key, owners)


def test_every_documented_shortcut_exists(window: MainWindow) -> None:
    keys = {k for k, _ in _sequences(window)}
    keys |= {"Ctrl+E", "Ctrl+H"}  # shown in the menu text; the shortcut lives in the catalog page
    for _group, items in strings.SHORTCUTS_GROUPS:
        for documented, _text in items:
            for part in re.split(r" / | … ", documented):
                part = part.replace("←", "Left").replace("→", "Right").replace("Enter", "Return")
                if part in ("Alt+6", "Alt+1", "Ctrl+1", "Ctrl+5"):
                    part = part  # ranges: their ends are checked
                if part.startswith("Ctrl+") and part[5:].isdigit():
                    continue  # the editor's Ctrl+1…5 live in the editor widget (checked elsewhere)
                assert part in keys or part == "Esc", documented


def test_f1_lists_the_shortcuts(qtbot, window: MainWindow) -> None:
    dialog = window.create_shortcuts_dialog()
    qtbot.addWidget(dialog)
    keys = dialog.shortcut_keys()
    assert "Ctrl+Enter" in keys and "Alt+← / Alt+→" in keys and "F1" in keys
    assert window.action_shortcuts.shortcut().toString() == "F1"


def test_buttons_carry_their_shortcut_in_the_tooltip(window: MainWindow) -> None:
    view = window.catalog_view
    assert "Alt+" in view.next_button.toolTip() and "Alt+" in view.prev_button.toolTip()
    assert all(f"Alt+{i + 1}" in b.toolTip() for i, b in enumerate(view.quick_buttons))
    assert "Ctrl+E" in view.editing.edit_button.toolTip()
    assert view.hint_label.isVisible() and "F1" in view.hint_label.text()


# ------------------------------------------------------------------ Georgian everywhere
ALLOWED_LATIN = {
    "SQLite", "DOCX", "TXT", "EVAR", "SHA", "Word", "USB", "ID", "Ctrl", "Alt", "Shift", "Enter", "Esc",
    "Return", "VascQBank", "Review", "Desktop", "PAD", "CLTI", "ALI", "AAA", "VENOUS", "AORTIC", "ARCH",
    "CAROTID", "VISCERAL", "DIALYSIS", "HEMODIALYSIS", "TRAUMA", "MESENTERIC", "RENAL", "BASIC", "SCIENCE",
    "TOS", "UPPER", "displaced", "backups", "database", "vascqbank", "sqlite", "json", "partial", "MB",
    "KB", "GB", "manual", "pre", "restore", "import", "migration", "FTS", "LIKE", "Qt", "PySide",
    "Windows", "Linux", "CachyOS", "OneDrive", "Sylfaen", "DejaVu", "Sans",  # names
}  # fmt: skip


_DATA = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"  # database ids
    r"|\b[\w.-]+\.(?:txt|docx|sqlite3|json)\b"  # file names
    r"|\(\.docx\)|_questions|_explanations"  # file-name patterns
)


def latin_words(text: str) -> set[str]:
    """Latin-script words that are not technical identifiers, file names or product names."""
    text = _DATA.sub(" ", text)
    return {w for w in re.findall(r"[A-Za-z]{2,}", text) if w not in ALLOWED_LATIN and not w.isupper()}


def visible_texts(root: QWidget) -> list[str]:
    texts: list[str] = []
    for widget in [root, *root.findChildren(QWidget)]:
        if not widget.isVisibleTo(root) and widget is not root:
            continue
        if isinstance(widget, QAbstractButton | QLabel):
            text = widget.text()
            if isinstance(widget, QLabel) and ("/" in text or "\\" in text):
                continue  # file paths are data, not interface text
            texts.append(text)
        if widget.toolTip() and "/" not in widget.toolTip():
            texts.append(widget.toolTip())
        if isinstance(widget, QComboBox):
            texts += [widget.itemText(i) for i in range(widget.count())]
    return [t for t in texts if t]


def test_the_interface_is_georgian(qtbot, window: MainWindow) -> None:
    offending: dict[str, set[str]] = {}
    for page in PAGES:
        window.show_page(page)
        for text in visible_texts(window):
            words = latin_words(re.sub(r"<[^>]+>", " ", text))
            if words:
                offending[text] = words
    for menu in window.menuBar().findChildren(type(window.menuBar().actions()[0].menu())):
        for action in menu.actions():
            if latin_words(action.text().split("\t")[0]):
                offending[action.text()] = latin_words(action.text())
    assert not offending, offending
    assert not latin_words(window.windowTitle())


def test_qt_dialog_texts_are_georgian(qtbot, styled_qapp) -> None:
    box = QMessageBox(QMessageBox.Icon.Information, "სათაური", "ტექსტი")
    box.setDetailedText("დეტალები")
    box.setStandardButtons(QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel)
    qtbot.addWidget(box)
    box.show()
    texts = {b.text() for b in box.buttons()}
    assert "დეტალების ჩვენება…" in texts and "Show Details..." not in texts
    assert {"კარგი", "გაუქმება"} <= texts
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No)
    qtbot.addWidget(buttons)
    assert {b.text().replace("&", "") for b in buttons.buttons()} == {"დიახ", "არა"}
    box.close()


def test_the_restore_dialog_and_shortcuts_dialog_are_georgian(qtbot, window: MainWindow, populated) -> None:
    for dialog in (RestoreDialog(populated, current=window._status), window.create_shortcuts_dialog()):
        qtbot.addWidget(dialog)
        dialog.show()
        assert not latin_words(dialog.windowTitle())
        bad = {t: latin_words(t) for t in visible_texts(dialog) if latin_words(t)}
        assert not bad, bad
        dialog.reject()


# ------------------------------------------------------------------ screen sizes
def _reachable(widget: QWidget, window: MainWindow) -> bool:
    """Inside the window, or inside a scroll area that can scroll it into view."""
    parent = widget.parentWidget()
    while parent is not None:
        if isinstance(parent, QScrollArea):
            return True
        parent = parent.parentWidget()
    top_left = widget.mapTo(window, widget.rect().topLeft())
    return window.rect().contains(QRect(top_left, widget.size()))


@pytest.mark.parametrize("size", SIZES, ids=lambda s: f"{s[0]}x{s[1]}")
def test_every_page_fits_every_target_screen(qtbot, window: MainWindow, size: tuple[int, int]) -> None:
    window.resize(*size)
    qtbot.waitUntil(lambda: window.width() <= size[0] and window.height() <= size[1], timeout=WAIT_MS)
    assert window.minimumSizeHint().width() <= size[0] and window.minimumSize().height() <= size[1] - 40
    assert window.catalog_view.go_to("ALI-0003")
    # the widest position text a full bank produces (the test bank has only 15 questions)
    window.catalog_view.position_label.setText(strings.CAT_POSITION.format(position="9 999", total="10 000"))
    for page in PAGES:
        window.show_page(page)
        QApplication.processEvents()
        page_widget = window.pages.currentWidget()
        for widget in [*page_widget.findChildren(QAbstractButton), *page_widget.findChildren(QComboBox)]:
            if not widget.isVisibleTo(window):
                continue
            assert _reachable(widget, window), (
                page,
                size,
                widget.objectName(),
                getattr(widget, "text", str)(),
            )
            if isinstance(widget, QAbstractButton) and widget.text():
                # never clipped: the button got at least the width its text needs
                # (sizeHint includes the style sheet's padding; minimumSizeHint does not)
                assert widget.width() >= widget.sizeHint().width() - 2, (page, size, widget.text())


def test_the_window_can_be_resized_by_the_user_without_losing_actions(qtbot, window: MainWindow) -> None:
    for width, height in [(1200, 700), (1600, 1000), (1280, 720)]:
        window.resize(width, height)
        QApplication.processEvents()
        view = window.catalog_view
        assert view.go_to("ALI-0002")
        assert _reachable(view.workflow.bar, window) and view.workflow.bar.isVisible()


# ------------------------------------------------------------------ focus and accessibility
def test_tab_order_is_logical_and_reaches_the_review_actions(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0001")
    view.search_edit.setFocus()
    order: list[QWidget] = []
    for _ in range(60):
        widget = QApplication.focusWidget()
        if widget is not None and widget not in order:
            order.append(widget)
        qtbot.keyClick(widget or window, Qt.Key.Key_Tab)
    assert view.search_edit in order and view.list_view in order and view.next_button in order
    assert order.index(view.search_edit) < order.index(view.list_view) < order.index(view.next_button)
    approve_buttons = [b for b in view.workflow.bar.findChildren(QAbstractButton) if b.isVisible()]
    assert any(b in order for b in approve_buttons)


def test_main_controls_have_accessible_names(window: MainWindow) -> None:
    view = window.catalog_view
    for widget in (view.list_view, view.search_edit, view.goto_edit, view.subject_combo, view.sort_combo):
        assert widget.accessibleName() and not latin_words(widget.accessibleName())
    assert all(b.accessibleName().startswith(strings.QF_GROUP_NAME) for b in view.quick_buttons)
    assert window.backup_view.table.accessibleName()


def test_status_is_written_out_not_only_coloured(window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0001")
    labels = [label.text() for label in view.detail_view.findChildren(QLabel)]
    assert ReviewStatus.PENDING_REVIEW.label_ka in labels


def test_medical_text_is_selectable(window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0001")
    stem = view.detail_view.stem_label
    flags = stem.textInteractionFlags()
    assert flags & Qt.TextInteractionFlag.TextSelectableByMouse


def test_disabled_actions_are_disabled_not_hidden_surprises(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("ALI-0001")
    assert not view.prev_button.isEnabled() and view.next_button.isEnabled()
    assert view.go_to("VENOUS-0005")
    assert view.prev_button.isEnabled() and not view.next_button.isEnabled()


# ------------------------------------------------------------------ long text, empty and partial banks
def test_a_very_long_question_is_shown_completely(qtbot, app_paths: AppPaths, styled_qapp) -> None:
    long_stem = " ".join(["ძალიან გრძელი სინთეზური კითხვის ტექსტი"] * 220)
    long_option = " ".join(["გრძელი ვარიანტი"] * 60)
    seed(app_paths, [make_question("ALI", 1, stem=long_stem, options=(long_option,) * 5)])
    window = open_window(qtbot, app_paths)
    window.resize(1280, 720)
    window.show_page(PAGE_CATALOG)
    view = window.catalog_view
    assert view.go_to("ALI-0001")
    QApplication.processEvents()
    assert view.detail_view.stem_label.plain_text == long_stem
    scroll = view.detail_view.findChild(QScrollArea)
    assert scroll is not None and scroll.verticalScrollBar().maximum() > 0  # scrollable, not clipped
    assert view.detail_view.stem_label.width() <= scroll.viewport().width()


def test_an_empty_database_is_usable(qtbot, app_paths: AppPaths, styled_qapp) -> None:
    window = open_window(qtbot, app_paths)
    counts = window.refresh_counts()
    assert counts.total == 0 and counts.total_drafts == 0
    assert window.welcome_view.empty_card.isVisibleTo(window.welcome_view)
    assert not window.welcome_view.subjects_card.isVisibleTo(window.welcome_view)
    window.show_page(PAGE_CATALOG)
    view = window.catalog_view
    for index in range(6):
        view.set_quick_filter(index)
        assert len(view.model.results) == 0
    view.next_question()
    assert view.current_uid is None
    window.show_page(PAGE_BACKUPS)
    assert not window.backup_view.restore_button.isEnabled()
    window.show_page(PAGE_EXPORT)


def test_a_partial_bank_shows_real_counts_and_targets_separately(
    qtbot, app_paths: AppPaths, styled_qapp
) -> None:
    seed_counts(app_paths, {"ALI": 7, "VENOUS": 3, "PAD_CLTI": 2})
    approve(app_paths, "ALI-0002")
    window = open_window(qtbot, app_paths)
    counts = window.refresh_counts()
    assert counts.total == 12  # never the 10,000 target
    table = window.welcome_view.subjects_table
    rows = {table.item(r, 0).text(): r for r in range(table.rowCount())}
    ali = next(s for s in SUBJECTS if s.code == "ALI")
    row = rows[ali.name_ka]
    headers = [table.horizontalHeaderItem(c).text() for c in range(table.columnCount())]
    cell = {h: table.item(row, c).text() for c, h in enumerate(headers) if c}
    assert cell[strings.HOME_COL_IMPORTED] == "7"
    assert cell[ReviewStatus.APPROVED.label_ka] == "1"
    assert cell[strings.HOME_COL_TARGET].replace(" ", "") == str(ali.target_count)
    empty_subject = next(s for s in SUBJECTS if s.code not in ("ALI", "VENOUS", "PAD_CLTI"))
    assert table.item(rows[empty_subject.name_ka], 1).text() == "0"
    assert window.bank_label.text() == strings.STATUS_BANK.format(total=12)


def test_status_changes_reach_the_strip_the_chips_and_the_home_table(qtbot, window: MainWindow) -> None:
    view = window.catalog_view
    assert view.go_to("VENOUS-0001") and view.workflow.approve()
    counts = window.refresh_counts()
    assert counts.subjects["VENOUS"].count(ReviewStatus.APPROVED) == 1
    table = window.welcome_view.subjects_table
    venous = next(s for s in SUBJECTS if s.code == "VENOUS")
    row = next(r for r in range(table.rowCount()) if table.item(r, 0).text() == venous.name_ka)
    approved_col = 2 + list(ReviewStatus).index(ReviewStatus.APPROVED)
    assert table.item(row, approved_col).text() == "1"
    view.set_quick_filter(3)
    assert listed_uids(window) == ["VENOUS-0001"]


def test_the_session_brings_back_the_question_and_the_status_filter(qtbot, populated: AppPaths) -> None:
    SettingsStore(populated.settings_file).set_session(
        SavedSession("ALI", ReviewStatus.PENDING_REVIEW, "ALI-0004")
    )
    window = open_window(qtbot, populated)
    view = window.catalog_view
    assert view.current_uid == "ALI-0004" and view.quick_filter_index == 1
    assert window.reviewer_profile.name == REVIEWER


# ------------------------------------------------------------------ regressions found in Phase 8
def test_shortcut_text_is_parsed_the_same_in_georgian(styled_qapp) -> None:
    """With Qt's Georgian catalogue, ``QKeySequence("Ctrl+Enter")`` reads as Ctrl+Return — the two
    approve shortcuts collided and neither fired. ``keys.key`` always parses the portable notation."""
    from vascqbank_desktop.ui.keys import key

    assert key("Ctrl+Enter") != key("Ctrl+Return")
    assert key("Ctrl+Shift+Enter") != key("Ctrl+Shift+Return")
    with pytest.raises(ValueError):
        key("")


def test_shortcuts_work_right_after_choosing_the_page_in_the_sidebar(qtbot, populated: AppPaths) -> None:
    from PySide6.QtWidgets import QListWidget

    window = open_window(qtbot, populated)
    qtbot.waitUntil(lambda: QApplication.activeWindow() is window, timeout=WAIT_MS)
    nav = window.sidebar.findChild(QListWidget, "NavList")
    nav.setFocus()  # the reviewer used the sidebar (mouse or keyboard)
    assert window.focusWidget() is nav
    window.sidebar.pageRequested.emit(PAGE_CATALOG)
    view = window.catalog_view
    focused = window.focusWidget()  # the window's focus child (also while the window is inactive)
    assert focused is not None and view.isAncestorOf(focused)
    assert view.go_to("ALI-0001")
    qtbot.keyClick(window.focusWidget(), Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert window.catalog_service.detail_by_uid("ALI-0001").status is ReviewStatus.APPROVED
    assert view.current_uid == "ALI-0002"
