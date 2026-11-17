"""The question editor inside the catalog page: editing, autosave, recovery, conflicts, failures.

Everything runs through the real services against a temporary database of synthetic questions;
the only thing replaced is the autosave delay (60 ms instead of one second).
"""

from __future__ import annotations

import threading
import time

import pytest

pytest.importorskip("PySide6")

import shiboken6  # noqa: E402
from bank import (  # noqa: E402
    approve,
    draft_row,
    limit_database_size,
    make_question,
    question_row,
    revisions_of,
    seed,
    seed_counts,
    simulate_edit,
    snapshot,
)
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.answers import AnswerLetter  # noqa: E402
from vascqbank_desktop.domain.editing import EditField  # noqa: E402
from vascqbank_desktop.domain.question import QuestionContent  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.services import draft_service, edit_service  # noqa: E402
from vascqbank_desktop.services.catalog_service import CatalogService  # noqa: E402
from vascqbank_desktop.services.draft_service import DraftService  # noqa: E402
from vascqbank_desktop.services.edit_errors import StorageFailure, StorageProblem  # noqa: E402
from vascqbank_desktop.services.edit_service import EditService  # noqa: E402
from vascqbank_desktop.services.question_stats import bank_counts  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.catalog_editing import EditingServices, EditMode  # noqa: E402
from vascqbank_desktop.ui.catalog_view import CatalogView  # noqa: E402
from vascqbank_desktop.ui.diff_view import CompareDialog  # noqa: E402
from vascqbank_desktop.ui.question_editor_view import CancelChoice, EditorState  # noqa: E402

pytestmark = pytest.mark.gui

WAIT_MS = 10_000
REVIEWER = "რამაზ აფხაძე"
UID = "ALI-0003"


class Env:
    """A catalog page wired to real editing services on a throw-away bank."""

    def __init__(self, qtbot, paths: AppPaths, *, reviewer: str | None = REVIEWER) -> None:
        self.paths = paths
        self.store = SettingsStore(paths.settings_file)
        self.profile = ReviewerProfile(self.store)
        if reviewer:
            self.profile.set_name(reviewer)
        self.drafts = DraftService(paths)
        self.edits = EditService(paths, self.profile)
        self.view = CatalogView(
            CatalogService(paths),
            SUBJECTS,
            editing=EditingServices(self.edits, self.drafts, self.profile),
            autosave_delay_ms=60,
            autosave_retry_ms=80,
        )
        qtbot.addWidget(self.view)
        self.view.resize(1200, 800)
        self.view.show()
        self.view.set_counts(bank_counts(paths))
        self.qtbot = qtbot

    # -- shortcuts for the tests ----------------------------------------------------------
    @property
    def editor(self):
        assert self.view.editing is not None
        return self.view.editing.editor

    @property
    def controller(self):
        assert self.view.editing is not None
        return self.view.editing

    def open(self, uid: str = UID) -> None:
        assert self.view.go_to(uid), uid
        self.qtbot.waitUntil(
            lambda: self.view.current_uid == uid and not self.view.is_busy(), timeout=WAIT_MS
        )

    def edit(self, uid: str = UID) -> None:
        self.open(uid)
        assert self.view.start_editing()
        assert self.view.is_editing

    def wait_state(self, state: EditorState) -> None:
        self.qtbot.waitUntil(lambda: self.editor.state is state, timeout=WAIT_MS)

    def type_stem(self, text: str) -> None:
        self.editor.stem_edit.setPlainText(text)

    def saved_content(self, uid: str = UID) -> QuestionContent:
        detail = CatalogService(self.paths).detail_by_uid(uid)
        assert detail is not None
        return detail.content


def type_text(widget, text: str) -> None:
    """Deliver *text* to a widget as real key events, one character at a time.

    (``QTest.keyClicks`` only understands Latin-1 and aborts the process on Georgian letters.)
    """
    for char in text:
        newline = char == "\n"
        key = Qt.Key.Key_Return if newline else Qt.Key.Key_unknown
        payload = "\r" if newline else char
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(widget, QKeyEvent(kind, key, Qt.KeyboardModifier.NoModifier, payload))


@pytest.fixture
def env(qtbot, styled_qapp, app_paths: AppPaths) -> Env:
    seed_counts(app_paths, {"ALI": 30, "VENOUS": 20})
    return Env(qtbot, app_paths)


# ------------------------------------------------------------------ entering and reading


def test_the_edit_button_opens_the_editor_on_the_saved_text(env: Env) -> None:
    env.open()
    detail = CatalogService(env.paths).detail_by_uid(UID)
    assert detail is not None
    assert env.controller.edit_button.isEnabled() and env.controller.history_button.isEnabled()
    env.controller.edit_button.click()
    assert env.view.is_editing and env.controller.mode is EditMode.EDIT
    assert env.view.mode_stack.currentWidget() is env.editor
    content = env.editor.content()
    assert content.stem == detail.content.stem
    assert content.options == detail.content.options
    assert content.correct == detail.content.correct
    assert content.explanation == detail.content.explanation
    assert env.editor.state is EditorState.CLEAN
    assert not env.controller.edit_button.isEnabled()  # no second editor on top of the first
    assert not env.controller.history_button.isEnabled()


def test_opening_the_editor_changes_nothing_in_the_database(env: Env) -> None:
    before = snapshot(env.paths)
    env.edit()
    env.editor.stem_edit.setFocus()
    env.view.editing.editor.cancel()  # nothing typed: no dialog, no draft
    assert snapshot(env.paths) == before
    assert draft_row(env.paths, UID) is None
    assert not env.view.is_editing


def test_exactly_one_answer_is_marked_correct_and_it_is_highlighted(env: Env) -> None:
    env.edit()
    detail = CatalogService(env.paths).detail_by_uid(UID)
    assert detail is not None
    index = detail.content.correct.index
    assert [row.radio.isChecked() for row in env.editor.option_rows].count(True) == 1
    assert env.editor.option_rows[index].radio.isChecked()
    assert [row.is_correct for row in env.editor.option_rows] == [i == index for i in range(5)]
    other = (index + 2) % 5
    env.editor.select_correct(other)
    assert [row.radio.isChecked() for row in env.editor.option_rows].count(True) == 1
    assert [row.is_correct for row in env.editor.option_rows] == [i == other for i in range(5)]
    assert env.editor.content().correct == AnswerLetter("აბგდე"[other])


def test_without_a_reviewer_name_the_editor_does_not_open(qtbot, styled_qapp, app_paths: AppPaths) -> None:
    seed_counts(app_paths, {"ALI": 5})
    bare = Env(qtbot, app_paths, reviewer=None)
    asked: list[bool] = []
    bare.view.reviewerRequired.connect(lambda: asked.append(True))
    bare.open("ALI-0002")
    assert not bare.view.start_editing()
    assert not bare.view.is_editing and asked == [True]
    assert strings.ED_NEED_REVIEWER in bare.view.notice_label.text()


# ------------------------------------------------------------------ explicit save


def _save_with_keyboard(env: Env) -> None:
    QTest.keyClick(env.editor.stem_edit, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)


@pytest.mark.parametrize("field", ["stem", "A", "B", "C", "D", "E", "explanation", "correct"])
def test_every_field_can_be_edited_and_saved_with_ctrl_s(env: Env, field: str) -> None:
    before = env.saved_content()
    env.edit()
    editor = env.editor
    new_text = f"ახალი ტექსტი — {field}\nმეორე ხაზი"
    if field == "stem":
        editor.stem_edit.setPlainText(new_text)
    elif field == "explanation":
        editor.explanation_edit.setPlainText(new_text)
    elif field == "correct":
        editor.select_correct((before.correct.index + 1) % 5)
    else:
        editor.option_rows["ABCDE".index(field)].edit.setPlainText(new_text)
    _save_with_keyboard(env)
    assert editor.state is EditorState.SAVED
    after = env.saved_content()
    expected = {
        "stem": before.stem,
        "explanation": before.explanation,
        "options": before.options,
        "correct": before.correct,
    }
    if field == "stem":
        expected["stem"] = new_text
    elif field == "explanation":
        expected["explanation"] = new_text
    elif field == "correct":
        expected["correct"] = AnswerLetter("აბგდე"[(before.correct.index + 1) % 5])
    else:
        options = list(before.options)
        options["ABCDE".index(field)] = new_text
        expected["options"] = tuple(options)
    assert (after.stem, after.options, after.correct, after.explanation) == (
        expected["stem"],
        expected["options"],
        expected["correct"],
        expected["explanation"],
    )
    revisions = revisions_of(env.paths, UID)
    assert [r["revision_no"] for r in revisions] == [1, 2]
    assert revisions[0]["origin"] == "IMPORT" and revisions[1]["origin"] == "EDIT"
    assert revisions[1]["author"] == REVIEWER
    assert question_row(env.paths, UID)["status"] == "IN_REVIEW"
    assert draft_row(env.paths, UID) is None  # the draft goes away with the commit


def test_saving_updates_the_read_only_view_the_list_and_the_counts(env: Env) -> None:
    saved: list[object] = []
    env.view.questionSaved.connect(saved.append)
    env.edit()
    env.type_stem("ახალი პირობა: რომელია სწორი?")
    env.editor.save()
    assert len(saved) == 1
    assert env.view.detail_view.detail is not None
    assert env.view.detail_view.detail.content.stem == "ახალი პირობა: რომელია სწორი?"
    assert env.view.detail_view.detail.revision_no == 2
    env.view.editing.editor.cancel()  # editing continues after a save; leave it
    assert not env.view.is_editing
    assert "ახალი პირობა: რომელია სწორი?" in env.view.detail_view.stem_label.text()


def test_saving_unchanged_text_creates_no_revision(env: Env) -> None:
    env.edit()
    env.editor.explanation_edit.setPlainText(env.editor.explanation_edit.toPlainText() + "  ")
    before = revisions_of(env.paths, UID)
    env.editor.save()
    assert revisions_of(env.paths, UID) == before
    assert env.editor.state is EditorState.CLEAN
    assert env.editor.state_label.text() == strings.ED_STATE_UNCHANGED


def test_georgian_multiline_text_round_trips_and_markup_is_never_interpreted(env: Env) -> None:
    text = "პირველი აბზაცი: ისხემია ≥ 50%\n\nმეორე აბზაცი — <b>არა ბოლდი</b> & „ციტატა“\n  ბოლო ხაზი ა-ბ-გ"
    env.edit()
    env.type_stem(text)
    env.editor.explanation_edit.setPlainText("განმარტება\nორ ხაზად")
    env.editor.save()
    stem = env.saved_content().stem
    assert stem == text.strip()  # stored exactly, only the outer whitespace is trimmed
    assert env.saved_content().explanation == "განმარტება\nორ ხაზად"
    env.view.editing.editor.cancel()
    shown = env.view.detail_view.stem_label.text()
    assert "&lt;b&gt;არა ბოლდი&lt;/b&gt;" in shown and "&amp;" in shown  # shown as text, never as markup
    assert "<b>" not in shown


def test_the_editor_accepts_real_keystrokes_in_georgian(env: Env) -> None:
    env.edit()
    editor = env.editor
    editor.stem_edit.clear()
    editor.stem_edit.setFocus()
    type_text(editor.stem_edit, "კითხვა ა")
    assert editor.stem_edit.toPlainText() == "კითხვა ა"
    type_text(editor.stem_edit, "\nბ")
    assert editor.stem_edit.toPlainText() == "კითხვა ა\nბ"  # Enter makes a new line, it never saves


def test_validation_blocks_the_save_and_names_the_problem_in_georgian(env: Env) -> None:
    env.edit()
    env.editor.option_rows[2].edit.setPlainText("   ")
    env.editor.stem_edit.setPlainText("")
    before = snapshot(env.paths)
    assert not env.editor.save()
    assert snapshot(env.paths) == before
    label = env.editor.validation_label
    assert label.isVisible() and strings.ED_VALIDATION_INTRO in label.text()
    assert (
        strings.ED_FIELD_STEM in label.text() and strings.ED_FIELD_OPTION.format(letter="გ") in label.text()
    )
    assert env.editor.stem_edit.property("invalid") is True
    assert env.editor.option_rows[2].edit.property("invalid") is True
    assert env.editor.option_rows[0].edit.property("invalid") in (None, False)
    assert env.editor.option_rows[2].edit.toPlainText() == "   "  # the text is still there
    env.editor.stem_edit.setPlainText("ახალი პირობა")  # typing clears the marks
    assert not env.editor.validation_label.isVisible()


def test_a_note_is_stored_with_the_review_event(env: Env) -> None:
    env.edit()
    env.type_stem("ახალი პირობა")
    env.editor.note_edit.setText("ტერმინოლოგია")
    env.editor.save()
    assert revisions_of(env.paths, UID)[-1]["note"] == "ტერმინოლოგია"


# ------------------------------------------------------------------ approved questions


def test_editing_an_approved_question_warns_and_asks_before_revoking(env: Env, monkeypatch) -> None:
    approved_no = approve(env.paths, UID)
    env.edit()
    assert env.editor.warning_label.isVisible()
    env.type_stem("შეცვლილი პირობა")
    env.wait_state(EditorState.DRAFT_SAVED)
    assert question_row(env.paths, UID)["status"] == "APPROVED"  # typing alone never changes approval
    asked: list[int] = []
    monkeypatch.setattr(env.editor, "confirm_revoke_approval", lambda n: asked.append(n) or False)
    assert not env.editor.save()
    assert asked == [approved_no]
    row = question_row(env.paths, UID)
    assert row["status"] == "APPROVED" and row["revision_count"] == 1
    assert draft_row(env.paths, UID) is not None  # the text is safe in the draft
    monkeypatch.setattr(env.editor, "confirm_revoke_approval", lambda n: True)
    assert env.editor.save()
    row = question_row(env.paths, UID)
    assert row["status"] == "IN_REVIEW" and row["approved_revision_id"] is None
    assert row["revision_count"] == 2
    assert not env.editor.warning_label.isVisible()


# ------------------------------------------------------------------ autosave


def test_typing_is_saved_as_a_draft_after_a_pause_and_never_as_a_revision(env: Env) -> None:
    env.edit()
    states: list[EditorState] = []
    env.editor.stateChanged.connect(states.append)
    env.type_stem("მონახაზის ტექსტი")
    assert env.editor.state is EditorState.UNSAVED  # nothing is written before the pause ends
    assert draft_row(env.paths, UID) is None
    env.wait_state(EditorState.DRAFT_SAVED)
    assert EditorState.UNSAVED in states
    row = draft_row(env.paths, UID)
    assert row is not None and row["stem"] == "მონახაზის ტექსტი" and row["reviewer_name"] == REVIEWER
    assert row["base_revision_id"] == revisions_of(env.paths, UID)[0]["id"]
    assert len(revisions_of(env.paths, UID)) == 1  # no revision per keystroke
    assert question_row(env.paths, UID)["status"] == "PENDING_REVIEW"  # drafts never change status
    assert strings.ED_STATE_DRAFT_SAVED.split("{")[0] in env.editor.state_label.text()


def test_many_keystrokes_become_one_draft_write(env: Env) -> None:
    env.edit()
    env.editor.stem_edit.clear()
    env.editor.stem_edit.setFocus()
    type_text(env.editor.stem_edit, "ტექსტი სწრაფად იწერება")
    env.wait_state(EditorState.DRAFT_SAVED)
    row = draft_row(env.paths, UID)
    assert row is not None and row["stem"] == "ტექსტი სწრაფად იწერება"
    assert row["write_count"] <= 3  # one trailing write, not one per keystroke
    assert len(revisions_of(env.paths, UID)) == 1


def test_a_draft_can_be_restored_after_leaving_and_the_marker_shows_in_the_list(env: Env) -> None:
    env.edit()
    env.type_stem("რჩება მონახაზად")
    env.wait_state(EditorState.DRAFT_SAVED)
    env.editor.ask_cancel_choice = lambda: CancelChoice.KEEP_DRAFT  # type: ignore[method-assign]
    assert env.editor.cancel()
    assert not env.view.is_editing
    assert env.saved_content().stem != "რჩება მონახაზად"  # the saved text is untouched
    assert draft_row(env.paths, UID) is not None
    ids = env.view.model.draft_ids
    assert CatalogService(env.paths).locate(UID).id in ids
    assert env.controller.banner.label.text() == strings.DR_BANNER.format(count=1)
    assert env.controller.draft_bar.isVisible()
    env.controller.draft_bar.restore_button.click()
    assert env.view.is_editing
    assert env.editor.stem_edit.toPlainText() == "რჩება მონახაზად"
    assert env.editor.info_label.isVisible()  # tells that this is a restored draft
    assert env.editor.state is EditorState.DRAFT_SAVED


def test_switching_questions_with_a_pending_autosave_persists_it_first(env: Env) -> None:
    env.edit()
    env.type_stem("ჯერ არ ჩაწერილი ტექსტი")
    assert env.editor.autosaver.pending and draft_row(env.paths, UID) is None
    assert env.view.go_to("ALI-0004")
    assert draft_row(env.paths, UID)["stem"] == "ჯერ არ ჩაწერილი ტექსტი"  # type: ignore[index]
    assert not env.view.is_editing and env.view.current_uid == "ALI-0004"
    assert draft_row(env.paths, "ALI-0004") is None


def test_next_and_previous_buttons_persist_the_draft_too(env: Env) -> None:
    env.edit()
    env.type_stem("ტექსტი ღილაკის წინ")
    env.view.next_button.click()
    assert draft_row(env.paths, UID)["stem"] == "ტექსტი ღილაკის წინ"  # type: ignore[index]
    assert env.view.current_uid == "ALI-0004" and not env.view.is_editing


def test_a_subject_switch_with_a_pending_autosave_persists_the_draft(env: Env) -> None:
    env.edit()
    env.type_stem("ტექსტი საგნის შეცვლამდე")
    env.view.set_subject("VENOUS")
    assert draft_row(env.paths, UID)["stem"] == "ტექსტი საგნის შეცვლამდე"  # type: ignore[index]


def test_a_draft_survives_being_filtered_out_of_the_list(env: Env) -> None:
    env.edit()
    env.type_stem("ფილტრით დამალული მონახაზი")
    env.wait_state(EditorState.DRAFT_SAVED)
    env.editor.ask_cancel_choice = lambda: CancelChoice.KEEP_DRAFT  # type: ignore[method-assign]
    env.editor.cancel()
    env.view.set_subject("VENOUS")
    env.view.set_search_text("ძებნის ტექსტი, რომელიც არაფერს ემთხვევა")
    env.qtbot.waitUntil(lambda: not env.view.is_busy(), timeout=WAIT_MS)
    assert draft_row(env.paths, UID) is not None
    assert env.drafts.count() == 1
    assert env.controller.banner.label.text() == strings.DR_BANNER.format(count=1)  # still counted


def test_leaving_the_editor_unchanged_removes_nothing_and_asks_nothing(env: Env, monkeypatch) -> None:
    env.edit()
    monkeypatch.setattr(env.editor, "ask_cancel_choice", lambda: pytest.fail("no dialog expected"))
    assert env.editor.cancel()
    assert not env.view.is_editing


def test_cancel_asks_what_to_do_with_unsaved_changes(env: Env, monkeypatch) -> None:
    env.edit()
    env.type_stem("ტექსტი, რომელიც გაუქმდება")
    env.wait_state(EditorState.DRAFT_SAVED)
    monkeypatch.setattr(env.editor, "ask_cancel_choice", lambda: CancelChoice.CONTINUE)
    assert not env.editor.cancel()
    assert env.view.is_editing and env.editor.stem_edit.toPlainText() == "ტექსტი, რომელიც გაუქმდება"
    monkeypatch.setattr(env.editor, "ask_cancel_choice", lambda: CancelChoice.DISCARD)
    assert env.editor.cancel()
    assert not env.view.is_editing
    assert draft_row(env.paths, UID) is None
    assert env.drafts.list_drafts() == []
    assert env.saved_content().stem != "ტექსტი, რომელიც გაუქმდება"
    assert len(revisions_of(env.paths, UID)) == 1  # discarding never touches the revisions


def test_escape_leaves_edit_mode(env: Env) -> None:
    env.edit()
    QTest.keyClick(env.editor.stem_edit, Qt.Key.Key_Escape)
    assert not env.view.is_editing


def test_ctrl_digits_choose_the_correct_answer_and_tab_walks_the_fields(env: Env) -> None:
    env.edit()
    for number in range(1, 6):
        QTest.keyClick(
            env.editor.stem_edit, getattr(Qt.Key, f"Key_{number}"), Qt.KeyboardModifier.ControlModifier
        )
        assert env.editor.content().correct == AnswerLetter("აბგდე"[number - 1])
    env.editor.stem_edit.setFocus()
    seen = []
    for _ in range(3):
        QTest.keyClick(env.editor.window().focusWidget(), Qt.Key.Key_Tab)
        seen.append(env.editor.window().focusWidget())
    assert seen[0] is env.editor.option_rows[0].edit
    assert seen[1] is env.editor.option_rows[1].edit
    assert seen[2] is env.editor.option_rows[2].edit  # Tab never inserts a tab character
    assert "\t" not in env.editor.stem_edit.toPlainText()


def test_revert_text_asks_and_puts_the_saved_text_back(env: Env, monkeypatch) -> None:
    original = env.saved_content()
    env.edit()
    env.type_stem("შეცდომით აკრეფილი")
    env.wait_state(EditorState.DRAFT_SAVED)
    assert env.editor.revert_button.isEnabled()
    monkeypatch.setattr(env.editor, "ask_revert_confirm", lambda: False)
    env.editor.revert_text()
    assert env.editor.stem_edit.toPlainText() == "შეცდომით აკრეფილი"
    monkeypatch.setattr(env.editor, "ask_revert_confirm", lambda: True)
    env.editor.revert_text()
    assert env.editor.stem_edit.toPlainText() == original.stem
    assert not env.editor.revert_button.isEnabled()
    env.wait_state(EditorState.CLEAN)
    assert env.editor.cancel()  # identical text: leaving removes the now pointless draft
    assert draft_row(env.paths, UID) is None


# ------------------------------------------------------------------ outdated drafts and conflicts


def test_a_draft_based_on_an_older_revision_is_flagged_and_never_overwrites_newer_text(env: Env) -> None:
    env.edit()
    env.type_stem("ჩემი მონახაზი ძველ ვერსიაზე")
    env.wait_state(EditorState.DRAFT_SAVED)
    env.editor.ask_cancel_choice = lambda: CancelChoice.KEEP_DRAFT  # type: ignore[method-assign]
    env.editor.cancel()
    simulate_edit(env.paths, UID, stem="სხვის მიერ შენახული ახალი ტექსტი")
    env.view.reload_current()
    bar = env.controller.draft_bar
    assert bar.isVisible() and "(1 → 2)" in bar.text_label.text()
    assert env.view.start_editing()
    assert env.editor.info_label.isVisible()
    assert env.editor.session.base_revision_no == 1 and env.editor.session.current_revision_no == 2
    assert env.editor.stem_edit.toPlainText() == "ჩემი მონახაზი ძველ ვერსიაზე"
    assert env.saved_content().stem == "სხვის მიერ შენახული ახალი ტექსტი"  # still the newer text
    assert env.editor.save() is False  # the stale base is detected: no silent overwrite
    assert env.editor.state is EditorState.CONFLICT
    assert env.saved_content().stem == "სხვის მიერ შენახული ახალი ტექსტი"
    assert len(revisions_of(env.paths, UID)) == 2


def test_a_conflict_keeps_my_text_and_offers_three_ways_out(env: Env) -> None:
    env.edit()
    env.type_stem("ჩემი ვერსია")
    simulate_edit(env.paths, UID, stem="უცხო ვერსია")  # somebody else saved meanwhile
    assert env.editor.save() is False
    editor = env.editor
    assert editor.state is EditorState.CONFLICT and editor.conflict_panel.isVisible()
    assert strings.ED_STATE_CONFLICT in editor.state_label.text()
    assert editor.stem_edit.toPlainText() == "ჩემი ვერსია"  # nothing lost
    assert env.saved_content().stem == "უცხო ვერსია"
    env.wait_state(EditorState.CONFLICT)
    # compare
    requested: list[object] = []
    editor.compareRequested.connect(lambda session, mine: requested.append((session, mine)))
    editor.conflict_compare_button.click()
    assert len(requested) == 1 and requested[0][1].stem == "ჩემი ვერსია"
    # knowingly save on top of the newer revision
    assert editor._save_over_newer()
    revisions = revisions_of(env.paths, UID)
    assert [r["revision_no"] for r in revisions] == [1, 2, 3]
    assert revisions[1]["stem"] == "უცხო ვერსია"  # the other revision stays in the history
    assert revisions[2]["stem"] == "ჩემი ვერსია"
    assert not editor.conflict_panel.isVisible()


def test_conflict_keep_as_draft_leaves_both_versions_safe(env: Env) -> None:
    env.edit()
    env.type_stem("ჩემი ვერსია დრაფტად")
    simulate_edit(env.paths, UID, stem="უცხო ვერსია")
    env.editor.save()
    env.editor.conflict_keep_button.click()
    assert not env.view.is_editing
    assert draft_row(env.paths, UID)["stem"] == "ჩემი ვერსია დრაფტად"  # type: ignore[index]
    assert env.saved_content().stem == "უცხო ვერსია"
    assert env.controller.draft_bar.isVisible()


# ------------------------------------------------------------------ failures keep the text


def test_a_failing_draft_write_is_shown_and_the_text_is_kept_and_retried(env: Env, monkeypatch) -> None:
    env.edit()
    real = env.drafts.save_draft
    calls: list[int] = []

    def failing(*args, **kwargs):
        calls.append(1)
        raise StorageFailure(StorageProblem.IO_ERROR, "simulated")

    monkeypatch.setattr(env.drafts, "save_draft", failing)
    env.type_stem("ტექსტი, რომელიც ვერ ჩაიწერა")
    env.wait_state(EditorState.ERROR)
    assert strings.ED_STATE_ERROR.split("{")[0].strip() in env.editor.state_label.text()
    assert strings.ED_STORAGE_RETAINED.strip() in env.editor.state_label.text()
    assert env.editor.stem_edit.toPlainText() == "ტექსტი, რომელიც ვერ ჩაიწერა"  # still in the field
    assert draft_row(env.paths, UID) is None
    monkeypatch.setattr(env.drafts, "save_draft", real)  # the problem goes away…
    env.wait_state(EditorState.DRAFT_SAVED)  # …and the automatic retry succeeds
    assert draft_row(env.paths, UID)["stem"] == "ტექსტი, რომელიც ვერ ჩაიწერა"  # type: ignore[index]
    assert len(calls) >= 1


def test_disk_full_while_typing_is_reported_and_nothing_is_claimed_saved(env: Env, monkeypatch) -> None:
    env.edit()
    limit_database_size(monkeypatch, draft_service)
    env.type_stem("ტექსტი " * 9000)  # about 60,000 characters: needs new pages
    env.wait_state(EditorState.ERROR)
    assert strings.ED_STORAGE_DISK_FULL in env.editor.state_label.text()
    assert draft_row(env.paths, UID) is None
    assert env.editor.state is not EditorState.DRAFT_SAVED
    assert len(env.editor.stem_edit.toPlainText()) > 50_000  # the typed text is untouched
    monkeypatch.undo()
    env.wait_state(EditorState.DRAFT_SAVED)
    assert draft_row(env.paths, UID) is not None


def test_leaving_when_the_draft_cannot_be_written_asks_and_can_keep_the_text(env: Env, monkeypatch) -> None:
    env.edit()
    monkeypatch.setattr(
        env.drafts,
        "save_draft",
        lambda *a, **k: (_ for _ in ()).throw(StorageFailure(StorageProblem.DISK_FULL, "full")),
    )
    env.type_stem("ტექსტი, რომლის დაკარგვაც არ შეიძლება")
    asked: list[str] = []
    monkeypatch.setattr(
        env.editor, "ask_continue_without_saving", lambda detail, closing=False: asked.append(detail) or False
    )
    env.view.go_to("ALI-0004")  # navigation is refused: the reviewer chose to keep the text
    assert asked and env.view.is_editing
    assert env.view.current_uid == UID
    assert env.editor.stem_edit.toPlainText() == "ტექსტი, რომლის დაკარგვაც არ შეიძლება"
    assert env.view.model.rowCount() > 0


def test_a_failing_explicit_save_keeps_the_text_the_draft_and_the_old_revision(env: Env, monkeypatch) -> None:
    env.edit()
    env.type_stem("შესანახი ტექსტი")
    env.wait_state(EditorState.DRAFT_SAVED)
    before = revisions_of(env.paths, UID)

    def failing(*args, **kwargs):
        raise StorageFailure(StorageProblem.DISK_FULL, "disk full")

    monkeypatch.setattr(env.edits, "save", failing)
    assert not env.editor.save()
    assert env.editor.state is EditorState.ERROR
    assert strings.ED_STORAGE_DISK_FULL in env.editor.state_label.text()
    assert env.editor.stem_edit.toPlainText() == "შესანახი ტექსტი"
    assert revisions_of(env.paths, UID) == before
    assert draft_row(env.paths, UID)["stem"] == "შესანახი ტექსტი"  # type: ignore[index]
    monkeypatch.undo()
    assert env.editor.save()
    assert len(revisions_of(env.paths, UID)) == 2


def test_a_real_disk_full_during_the_save_rolls_back_and_a_retry_works(env: Env, monkeypatch) -> None:
    env.edit()
    env.type_stem("ბ" * 20_000)
    env.editor.explanation_edit.setPlainText("ა" * 20_000)
    env.wait_state(EditorState.DRAFT_SAVED)
    before = snapshot(env.paths)
    limit_database_size(monkeypatch, edit_service)
    assert not env.editor.save()
    assert env.editor.state is EditorState.ERROR
    after = snapshot(env.paths)
    assert after == before  # not one table changed
    monkeypatch.undo()
    assert env.editor.save()
    assert len(revisions_of(env.paths, UID)) == 2


# ------------------------------------------------------------------ shutdown and worker lifetime


def test_closing_with_a_pending_autosave_writes_the_draft(env: Env) -> None:
    env.edit()
    env.type_stem("ბოლო წამს აკრეფილი")
    assert env.view.prepare_shutdown()
    assert draft_row(env.paths, UID)["stem"] == "ბოლო წამს აკრეფილი"  # type: ignore[index]
    assert not env.view.is_editing


def test_closing_during_a_slow_draft_write_waits_for_it_and_loses_nothing(env: Env, monkeypatch) -> None:
    env.edit()
    started, release = threading.Event(), threading.Event()
    real = env.drafts.save_draft

    def slow(*args, **kwargs):
        started.set()
        release.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(env.drafts, "save_draft", slow)
    env.type_stem("ტექსტი ნელი ჩაწერისას")
    assert started.wait(5) or env.qtbot.waitUntil(started.is_set, timeout=WAIT_MS) is None
    env.type_stem("ტექსტი ნელი ჩაწერისას — კიდევ უფრო ახალი")  # typed while the write is running
    threading.Timer(0.25, release.set).start()
    began = time.monotonic()
    assert env.view.prepare_shutdown()  # blocks until both texts are safely written
    assert time.monotonic() - began >= 0.2
    assert draft_row(env.paths, UID)["stem"] == "ტექსტი ნელი ჩაწერისას — კიდევ უფრო ახალი"  # type: ignore[index]


def test_closing_is_refused_when_the_text_cannot_be_saved_and_the_user_keeps_it(
    env: Env, monkeypatch
) -> None:
    env.edit()
    monkeypatch.setattr(
        env.drafts,
        "save_draft",
        lambda *a, **k: (_ for _ in ()).throw(StorageFailure(StorageProblem.IO_ERROR, "x")),
    )
    env.type_stem("ტექსტი, რომელიც ვერ შეინახა")
    monkeypatch.setattr(env.editor, "ask_continue_without_saving", lambda detail, closing=False: False)
    assert not env.view.prepare_shutdown()
    assert env.view.is_editing and env.editor.stem_edit.toPlainText() == "ტექსტი, რომელიც ვერ შეინახა"
    monkeypatch.setattr(env.editor, "ask_continue_without_saving", lambda detail, closing=False: True)
    assert env.view.prepare_shutdown()  # the reviewer knowingly gave the text up


def test_deleting_the_page_during_a_write_is_safe(env: Env, monkeypatch, qtbot, request) -> None:
    env.edit()
    started, release = threading.Event(), threading.Event()
    real = env.drafts.save_draft

    def slow(*args, **kwargs):
        started.set()
        release.wait(5)
        return real(*args, **kwargs)

    monkeypatch.setattr(env.drafts, "save_draft", slow)
    env.type_stem("ტექსტი წაშლის დროს")
    qtbot.waitUntil(started.is_set, timeout=WAIT_MS)
    view = env.view
    view.close()
    shiboken6.delete(view)  # the C++ page is gone while the worker is still inside the database call
    request.node.qt_widgets.clear()  # (pytest-qt would otherwise try to close the dead widget)
    release.set()
    for _ in range(20):  # let the finished worker deliver its result to a (possibly) dead page
        QTest.qWait(20)
    assert draft_row(env.paths, UID) is not None  # the write completed regardless


# ------------------------------------------------------------------ history


def test_history_lists_every_revision_read_only_and_compares_with_the_current(env: Env) -> None:
    env.edit()
    env.type_stem("პირველი რედაქცია")
    env.editor.save()
    env.type_stem("მეორე რედაქცია")
    env.editor.note_edit.setText("სტილი")
    env.editor.save()
    env.editor.cancel()
    before = snapshot(env.paths)
    assert env.view.show_history()
    history = env.controller.history
    assert env.controller.mode is EditMode.HISTORY and not env.controller.edit_button.isEnabled()
    assert [r.revision_no for r in history.revisions] == [3, 2, 1]
    assert history.revisions[0].is_current and history.revisions[0].author == REVIEWER
    history.select_revision_number(1)
    assert history.selected_revision is not None
    assert history.selected_revision.content.stem != "მეორე რედაქცია"
    assert history.diff_check.isChecked()
    history.diff_check.setChecked(False)
    assert history.selected_revision.content.stem in history.text_label.text()  # plain text of revision 1
    history.back_button.click()
    assert env.controller.mode is EditMode.READ and env.controller.edit_button.isEnabled()
    assert snapshot(env.paths) == before  # looking never changes anything


def test_the_draft_list_shows_every_draft_and_opens_the_question(env: Env) -> None:
    for uid, text in (("ALI-0003", "ა"), ("ALI-0005", "ბ"), ("VENOUS-0002", "გ")):
        env.edit(uid)
        env.type_stem(f"მონახაზი {text}")
        env.editor.flush_draft()
        env.editor.ask_cancel_choice = lambda: CancelChoice.KEEP_DRAFT  # type: ignore[method-assign]
        env.editor.cancel()
    dialog = env.controller.open_draft_list()
    assert len(dialog.rows) == 3 and dialog.table.rowCount() == 3
    assert {row.uid for row in dialog.rows} == {"ALI-0003", "ALI-0005", "VENOUS-0002"}
    dialog.table.selectRow(1)
    chosen = dialog.selected_row()
    assert chosen is not None
    dialog.open_button.click()
    assert env.view.is_editing and env.view.current_uid == chosen.uid
    assert env.editor.stem_edit.toPlainText().startswith("მონახაზი")
    dialog.close()


def test_discarding_a_draft_is_deliberate_archived_and_confirmed(env: Env, monkeypatch) -> None:
    env.edit()
    env.type_stem("გასაუქმებელი მონახაზი")
    env.wait_state(EditorState.DRAFT_SAVED)
    env.editor.ask_cancel_choice = lambda: CancelChoice.KEEP_DRAFT  # type: ignore[method-assign]
    env.editor.cancel()
    question_id = env.view.current_question_id
    assert question_id is not None
    monkeypatch.setattr(env.controller, "confirm_discard_draft", lambda uid: False)
    assert not env.controller.discard_draft(question_id, UID)
    assert draft_row(env.paths, UID) is not None  # declined: nothing happens
    monkeypatch.setattr(env.controller, "confirm_discard_draft", lambda uid: True)
    assert env.controller.discard_draft(question_id, UID)
    assert draft_row(env.paths, UID) is None
    assert env.drafts.count() == 0
    assert not env.controller.draft_bar.isVisible()


def test_nothing_else_in_the_bank_changes_while_one_question_is_edited(env: Env) -> None:
    others = [make_question("ALI", n) for n in range(100, 102)]
    seed(env.paths, others)
    snap_before = snapshot(env.paths)
    env.edit()
    env.type_stem("ერთი კითხვის შეცვლა")
    env.editor.save()
    snap_after = snapshot(env.paths)
    changed_questions = [
        a[1] for a, b in zip(snap_before["questions"], snap_after["questions"], strict=True) if a != b
    ]
    assert len(changed_questions) == 1
    assert len(snap_after["question_revisions"]) == len(snap_before["question_revisions"]) + 1
    assert len(snap_after["review_events"]) == len(snap_before["review_events"]) + 1


# ------------------------------------------------------------------ comparisons


def test_the_draft_bar_compares_the_draft_with_the_saved_text(env: Env) -> None:
    env.edit()
    env.type_stem("შესადარებელი მონახაზი")
    env.wait_state(EditorState.DRAFT_SAVED)
    env.editor.ask_cancel_choice = lambda: CancelChoice.KEEP_DRAFT  # type: ignore[method-assign]
    env.editor.cancel()
    env.controller.draft_bar.compare_button.click()
    dialog = env.controller._dialogs[-1]
    assert isinstance(dialog, CompareDialog)
    assert dialog.view.changed_fields == (EditField.STEM,)  # only the typed field differs
    dialog.close()
    assert dialog not in env.controller._dialogs
    assert draft_row(env.paths, UID) is not None  # comparing changes nothing


def test_the_conflict_panel_compares_my_text_with_the_newer_revision(env: Env) -> None:
    env.edit()
    env.type_stem("ჩემი ვერსია შედარებისთვის")
    simulate_edit(env.paths, UID, explanation="სხვის მიერ შეცვლილი განმარტება")
    env.editor.save()
    env.editor.conflict_compare_button.click()
    dialog = env.controller._dialogs[-1]
    assert isinstance(dialog, CompareDialog)
    assert EditField.STEM in dialog.view.changed_fields
    assert EditField.EXPLANATION in dialog.view.changed_fields  # the other reviewer's change shows too
    dialog.close()
    assert env.view.is_editing and env.editor.stem_edit.toPlainText() == "ჩემი ვერსია შედარებისთვის"
