"""The editorial workflow on the catalog page: actions, approve-and-next, restore, errors.

Real services on a temporary database of synthetic questions; only the modal dialogs are
replaced (they are methods of the workflow controller, so a test answers them directly).
"""

from __future__ import annotations

import re

import pytest

pytest.importorskip("PySide6")

from bank import (  # noqa: E402
    draft_row,
    events_of,
    question_row,
    revisions_of,
    seed_counts,
    simulate_edit,
    snapshot,
)
from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QShortcut  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from vascqbank_desktop.config.paths import AppPaths  # noqa: E402
from vascqbank_desktop.domain.review_status import ReviewStatus  # noqa: E402
from vascqbank_desktop.domain.subjects import SUBJECTS  # noqa: E402
from vascqbank_desktop.domain.workflow import ReviewAction  # noqa: E402
from vascqbank_desktop.services.catalog_service import CatalogService  # noqa: E402
from vascqbank_desktop.services.draft_service import DraftService  # noqa: E402
from vascqbank_desktop.services.edit_errors import StorageFailure, StorageProblem  # noqa: E402
from vascqbank_desktop.services.edit_service import EditService  # noqa: E402
from vascqbank_desktop.services.question_stats import bank_counts  # noqa: E402
from vascqbank_desktop.services.review_service import ReviewService  # noqa: E402
from vascqbank_desktop.services.settings_service import ReviewerProfile, SettingsStore  # noqa: E402
from vascqbank_desktop.ui import strings  # noqa: E402
from vascqbank_desktop.ui.catalog_editing import EditingServices, EditMode  # noqa: E402
from vascqbank_desktop.ui.catalog_view import CatalogView  # noqa: E402
from vascqbank_desktop.ui.catalog_workflow import RestoreChoice  # noqa: E402
from vascqbank_desktop.ui.question_editor_view import EditorState  # noqa: E402
from vascqbank_desktop.ui.workflow_widgets import ReasonDialog  # noqa: E402

pytestmark = pytest.mark.gui

WAIT_MS = 10_000
REVIEWER = "რამაზ აფხაძე"
UID = "ALI-0003"


class Flow:
    """A catalog page wired to every real service, including the review service."""

    def __init__(self, qtbot, paths: AppPaths, *, reviewer: str | None = REVIEWER) -> None:
        self.paths = paths
        self.qtbot = qtbot
        self.profile = ReviewerProfile(SettingsStore(paths.settings_file))
        if reviewer:
            self.profile.set_name(reviewer)
        self.drafts = DraftService(paths)
        self.edits = EditService(paths, self.profile)
        self.review = ReviewService(paths, self.profile)
        self.catalog = CatalogService(paths)
        self.view = CatalogView(
            self.catalog,
            SUBJECTS,
            editing=EditingServices(self.edits, self.drafts, self.profile, self.review),
            autosave_delay_ms=60,
            autosave_retry_ms=80,
        )
        qtbot.addWidget(self.view)
        self.view.resize(1200, 860)
        self.view.show()
        self.view.set_counts(bank_counts(paths))
        self.saved: list[object] = []
        self.view.questionSaved.connect(self.saved.append)

    @property
    def wf(self):
        assert self.view.workflow is not None
        return self.view.workflow

    @property
    def editing(self):
        assert self.view.editing is not None
        return self.view.editing

    def open(self, uid: str = UID) -> None:
        assert self.view.go_to(uid)
        self.qtbot.waitUntil(
            lambda: self.view.current_uid == uid and not self.view.is_busy(), timeout=WAIT_MS
        )

    def settle(self) -> None:
        self.qtbot.waitUntil(lambda: not self.view.is_busy(), timeout=WAIT_MS)

    def listed(self) -> list[str]:
        rows = (self.view.model.summary_at(r) for r in range(self.view.model.rowCount()))
        return [s.uid for s in rows if s is not None]

    def row(self, uid: str = UID) -> dict:
        return question_row(self.paths, uid)

    def notice(self) -> tuple[str, str]:
        label = self.view.notice_label
        return label.objectName(), label.text()

    def actions(self) -> set[ReviewAction]:
        return self.wf.bar.visible_actions()

    def answer(self, **replies) -> None:
        """Preset the dialogs: e.g. ``answer(ask_reject_reason="ტექსტი")``."""
        for name, value in replies.items():
            setattr(self.wf, name, (lambda *a, _v=value, **k: _v))


@pytest.fixture
def flow(qtbot, styled_qapp, app_paths: AppPaths) -> Flow:
    seed_counts(app_paths, {"ALI": 12, "VENOUS": 6})
    return Flow(qtbot, app_paths)


# ------------------------------------------------------------------ the action bar


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (ReviewStatus.PENDING_REVIEW, {ReviewAction.START_REVIEW, ReviewAction.APPROVE, ReviewAction.REJECT}),
        (ReviewStatus.IN_REVIEW, {ReviewAction.APPROVE, ReviewAction.REJECT}),
        (ReviewStatus.APPROVED, {ReviewAction.REJECT, ReviewAction.REOPEN}),
        (ReviewStatus.REJECTED, {ReviewAction.REOPEN}),
    ],
)
def test_the_bar_offers_exactly_the_actions_of_the_status(flow: Flow, status, expected) -> None:
    from bank import set_status

    if status is not ReviewStatus.PENDING_REVIEW:
        set_status(flow.paths, UID, status)
    flow.open()
    assert flow.actions() == expected
    assert flow.wf.bar.isVisible()


def test_the_buttons_carry_the_required_georgian_labels(flow: Flow) -> None:
    flow.open()
    bar = flow.wf.bar
    assert bar.approve_button.text() == "დამტკიცება"
    assert bar.approve_next_button.text() == "დამტკიცება და შემდეგი →"
    assert bar.reject_button.text() == "უარყოფა"
    assert bar.reopen_button.text() == "ხელახლა გახსნა"
    assert bar.start_button.text() == "განხილვის დაწყება"
    assert flow.view.next_button.text() == strings.CAT_NEXT  # the plain "next" stays too
    assert flow.editing.history.restore_button.text() == "წინა რედაქციის დაბრუნება"


def test_the_bar_follows_the_open_question_and_hides_in_the_history(flow: Flow) -> None:
    flow.open()
    assert flow.actions() == {ReviewAction.START_REVIEW, ReviewAction.APPROVE, ReviewAction.REJECT}
    assert flow.view.show_history() and not flow.wf.bar.isVisible()
    flow.editing.close_history()
    assert flow.wf.bar.isVisible()
    flow.view.next_question()
    assert flow.view.current_uid == "ALI-0004" and flow.wf.bar.isVisible()


def test_nothing_is_offered_without_a_review_service(qtbot, styled_qapp, app_paths: AppPaths) -> None:
    seed_counts(app_paths, {"ALI": 3})
    view = CatalogView(CatalogService(app_paths), SUBJECTS)
    qtbot.addWidget(view)
    assert view.workflow is None and view.editing is None


# ------------------------------------------------------------------ approve


def test_approving_updates_everything_at_once(flow: Flow) -> None:
    flow.open()
    flow.wf.bar.approve_button.click()
    row = flow.row()
    assert row["status"] == "APPROVED" and row["approved_revision_id"] == row["current_revision_id"]
    assert flow.view.detail_view.detail.status is ReviewStatus.APPROVED
    assert flow.view.detail_view.status_chip.text() == ReviewStatus.APPROVED.label_ka
    kind, text = flow.notice()
    assert kind == "SuccessNotice" and text == f"{UID} — ✓ დამტკიცებულია — რედაქცია 1"
    assert len(flow.saved) == 1 and flow.saved[0].action == "APPROVE"
    assert flow.actions() == {ReviewAction.REJECT, ReviewAction.REOPEN}
    flow.qtbot.waitUntil(
        lambda: any(
            (s := flow.view.model.summary_at(r)) is not None
            and s.uid == UID
            and s.status is ReviewStatus.APPROVED
            for r in range(flow.view.model.rowCount())
        ),
        timeout=WAIT_MS,
    )  # the list row shows the new status as well


def test_an_approved_question_cannot_be_approved_again_from_the_ui(flow: Flow) -> None:
    flow.open()
    assert flow.wf.approve()
    before = snapshot(flow.paths)
    assert not flow.wf.approve()  # no button, and the shortcut path refuses with a message
    kind, text = flow.notice()
    assert kind == "ErrorNotice" and "დამტკიცებული" in text
    assert snapshot(flow.paths) == before


def test_approve_and_next_moves_only_after_the_commit(flow: Flow) -> None:
    flow.open()
    order: list[str] = []
    real = flow.review.approve

    def spy(*args, **kwargs):
        order.append("approve")
        result = real(*args, **kwargs)
        order.append(f"committed:{flow.row()['status']}")  # the database already says APPROVED
        return result

    flow.review.approve = spy  # type: ignore[method-assign]
    flow.view.questionOpened.connect(lambda uid: order.append(f"opened:{uid}"))
    flow.wf.bar.approve_next_button.click()
    assert order == ["approve", "committed:APPROVED", "opened:ALI-0004"]
    assert flow.view.current_uid == "ALI-0004" and flow.row()["status"] == "APPROVED"
    assert flow.row("ALI-0004")["status"] == "PENDING_REVIEW"  # and the next one is untouched
    assert "ALI-0003" in flow.notice()[1]


def test_approve_and_next_continues_predictably_when_the_question_leaves_the_filter(flow: Flow) -> None:
    flow.view.set_status(ReviewStatus.PENDING_REVIEW)
    flow.settle()
    flow.open()
    flow.view.next_question()  # ALI-0004
    assert flow.view.current_uid == "ALI-0004"
    flow.wf.approve_and_next()
    assert flow.view.current_uid == "ALI-0005"  # the old list's next question, not the list's first
    flow.settle()
    assert "ALI-0004" not in flow.listed() and "ALI-0005" in flow.listed()
    assert flow.row("ALI-0004")["status"] == "APPROVED"
    flow.wf.approve_and_next()  # and again
    assert flow.view.current_uid == "ALI-0006"


def test_approve_and_next_at_the_end_of_the_list_stays_and_says_so(flow: Flow) -> None:
    flow.view.set_subject("VENOUS")
    flow.settle()
    flow.open("VENOUS-0006")
    assert flow.wf.approve_and_next()
    assert flow.view.current_uid == "VENOUS-0006" and flow.row("VENOUS-0006")["status"] == "APPROVED"
    assert strings.WF_LAST_QUESTION in flow.notice()[1]


def test_a_failed_approval_never_navigates(flow: Flow) -> None:
    flow.open()
    simulate_edit(flow.paths, UID, stem="სხვის მიერ შენახული ტექსტი")  # changed after it was displayed
    before = snapshot(flow.paths)
    assert not flow.wf.approve_and_next()
    assert flow.view.current_uid == UID
    kind, text = flow.notice()
    assert kind == "ErrorNotice" and "კითხვა სხვაგან შეიცვალა" in text and "არაფერი ჩაწერილა" in text
    assert snapshot(flow.paths) == before  # the unseen text was not approved
    assert flow.view.detail_view.detail.revision_no == 2  # the screen now shows what is really saved
    assert flow.wf.approve_and_next() and flow.view.current_uid == "ALI-0004"  # reviewed, then approved


def test_an_unsaved_draft_blocks_the_approval_and_offers_to_open_it(flow: Flow) -> None:
    flow.open()
    d = flow.catalog.detail_by_uid(UID)
    draft = type(d.content)("მონახაზი", d.content.options, d.content.correct, "e")
    flow.drafts.save_draft(d.id, d.revision_id, d.lock_version, draft, REVIEWER)
    flow.view.reload_current()
    asked: list[object] = []
    flow.wf.ask_open_draft = lambda error: asked.append(error) or False  # type: ignore[method-assign]
    before = snapshot(flow.paths)
    assert not flow.wf.approve()
    assert len(asked) == 1 and asked[0].reviewer_name == REVIEWER
    assert snapshot(flow.paths) == before and draft_row(flow.paths, UID) is not None
    flow.wf.ask_open_draft = lambda error: True  # type: ignore[method-assign]
    assert not flow.wf.approve()
    assert flow.view.is_editing and flow.editing.editor.stem_edit.toPlainText() == "მონახაზი"


def test_invalid_saved_text_blocks_the_approval_with_a_georgian_explanation(flow: Flow) -> None:
    simulate_edit(flow.paths, UID, stem="ა" * 20_001)
    flow.open()
    assert not flow.wf.approve()
    kind, text = flow.notice()
    assert kind == "ErrorNotice" and "ვერ გაიარა შემოწმება" in text and flow.row()["status"] == "IN_REVIEW"


def test_without_a_reviewer_name_nothing_can_be_decided(qtbot, styled_qapp, app_paths: AppPaths) -> None:
    seed_counts(app_paths, {"ALI": 4})
    bare = Flow(qtbot, app_paths, reviewer=None)
    asked: list[bool] = []
    bare.view.reviewerRequired.connect(lambda: asked.append(True))
    bare.open("ALI-0002")
    before = snapshot(app_paths)
    for call in (
        bare.wf.approve,
        bare.wf.approve_and_next,
        bare.wf.reject,
        bare.wf.reopen,
        bare.wf.start_review,
    ):
        assert not call()
    assert len(asked) >= 3 and snapshot(app_paths) == before
    assert bare.notice()[1] == strings.ED_NEED_REVIEWER


# ------------------------------------------------------------------ approve from the editor


def edit_stem(flow: Flow, text: str) -> None:
    flow.open()
    assert flow.view.start_editing()
    flow.editing.editor.stem_edit.setPlainText(text)


def test_approving_while_editing_saves_the_text_first_and_approves_that_revision(flow: Flow) -> None:
    edit_stem(flow, "შესწორებული პირობა")
    flow.answer(ask_save_and_approve=True)
    assert flow.wf.approve()
    row = flow.row()
    assert row["status"] == "APPROVED" and row["revision_count"] == 2
    approved = flow.catalog.revision(row["approved_revision_id"])
    assert approved.info.revision_no == 2 and approved.content.stem == "შესწორებული პირობა"
    assert [e["action"] for e in events_of(flow.paths, UID)] == ["IMPORTED", "EDITED", "APPROVE"]
    assert not flow.view.is_editing and draft_row(flow.paths, UID) is None


def test_declining_the_save_keeps_the_editor_and_approves_nothing(flow: Flow) -> None:
    edit_stem(flow, "ტექსტი, რომელიც არ შეინახა")
    flow.answer(ask_save_and_approve=False)
    before = snapshot(flow.paths)
    assert not flow.wf.approve()
    assert (
        flow.view.is_editing and flow.editing.editor.stem_edit.toPlainText() == "ტექსტი, რომელიც არ შეინახა"
    )
    assert snapshot(flow.paths) == before


def test_an_invalid_edit_is_not_saved_and_not_approved(flow: Flow) -> None:
    edit_stem(flow, "   ")
    flow.answer(ask_save_and_approve=True)
    assert not flow.wf.approve()
    assert flow.view.is_editing and flow.editing.editor.validation_label.isVisible()
    assert flow.row()["status"] == "PENDING_REVIEW" and flow.row()["revision_count"] == 1


def test_approve_and_next_never_navigates_when_the_edit_cannot_be_saved(flow: Flow, monkeypatch) -> None:
    edit_stem(flow, "ტექსტი, რომლისთვისაც ადგილი არ არის")
    flow.answer(ask_save_and_approve=True)

    def failing(*args, **kwargs):
        raise StorageFailure(StorageProblem.DISK_FULL, "full")

    monkeypatch.setattr(flow.edits, "save", failing)
    assert not flow.wf.approve_and_next()
    assert flow.view.is_editing and flow.view.current_uid == UID  # still here, text intact
    assert flow.editing.editor.stem_edit.toPlainText() == "ტექსტი, რომლისთვისაც ადგილი არ არის"
    assert flow.editing.editor.state is EditorState.ERROR and flow.row()["status"] == "PENDING_REVIEW"


def test_approve_and_next_from_the_editor_saves_approves_then_moves_on(flow: Flow) -> None:
    edit_stem(flow, "პირობა, რომელიც დამტკიცდა")
    flow.answer(ask_save_and_approve=True)
    assert flow.wf.approve_and_next()
    assert flow.view.current_uid == "ALI-0004" and not flow.view.is_editing
    assert flow.row()["status"] == "APPROVED" and flow.row()["revision_count"] == 2


def test_an_editor_without_changes_just_closes_and_approves_the_displayed_text(flow: Flow) -> None:
    flow.open()
    assert flow.view.start_editing()
    flow.answer(ask_save_and_approve=lambda: pytest.fail("nothing to save: no question expected"))
    assert flow.wf.approve()
    assert (
        not flow.view.is_editing and flow.row()["status"] == "APPROVED" and flow.row()["revision_count"] == 1
    )


# ------------------------------------------------------------------ reject


def test_rejection_asks_for_a_reason_and_records_it(flow: Flow) -> None:
    flow.open()
    flow.answer(ask_reject_reason=None)
    assert not flow.wf.reject() and flow.row()["status"] == "PENDING_REVIEW"  # cancelled
    flow.answer(ask_reject_reason="პასუხი გ არასწორია")
    assert flow.wf.reject()
    row = flow.row()
    assert row["status"] == "REJECTED" and row["approved_revision_id"] is None
    assert events_of(flow.paths, UID)[-1]["note"] == "პასუხი გ არასწორია"
    assert flow.actions() == {ReviewAction.REOPEN}
    kind, text = flow.notice()
    assert kind == "SuccessNotice" and text.endswith("უარყოფილია")


def test_rejecting_an_approved_question_warns_and_clears_the_approval(flow: Flow) -> None:
    flow.open()
    assert flow.wf.approve()
    seen: list[bool] = []
    flow.wf.ask_reject_reason = lambda *, approved: seen.append(approved) or "მიზეზი"  # type: ignore[method-assign]
    assert flow.wf.reject()
    assert seen == [True] and flow.row()["approved_revision_id"] is None
    assert "დამტკიცება გაუქმდა" in flow.notice()[1]


def test_the_reason_dialog_will_not_accept_an_empty_reason(qtbot, styled_qapp) -> None:
    dialog = ReasonDialog(
        strings.WF_REJECT_TITLE,
        strings.WF_REJECT_INTRO,
        confirm_text=strings.WF_REJECT_CONFIRM,
        required=True,
        warning=strings.WF_REJECT_APPROVED_WARNING,
        destructive=True,
    )
    qtbot.addWidget(dialog)
    dialog.show()
    dialog.edit.setPlainText("   \n  ")
    dialog.confirm_button.click()
    assert dialog.isVisible() and dialog.error_label.isVisible()
    assert dialog.error_label.text() == strings.WF_REASON_REQUIRED
    dialog.edit.setPlainText("პირველი ხაზი\nმეორე   ხაზი")
    assert not dialog.error_label.isVisible()
    dialog.confirm_button.click()
    assert dialog.result() == dialog.DialogCode.Accepted and dialog.text() == "პირველი ხაზი მეორე ხაზი"
    optional = ReasonDialog("t", "i", confirm_text="ok", required=False)
    qtbot.addWidget(optional)
    optional.show()
    optional.confirm_button.click()
    assert optional.result() == optional.DialogCode.Accepted and optional.text() == ""


def test_rejecting_from_the_editor_keeps_the_unsaved_text_as_a_draft(flow: Flow) -> None:
    edit_stem(flow, "ტექსტი, რომელიც არ უნდა დაიკარგოს")
    flow.answer(ask_reject_reason="მიზეზი")
    assert flow.wf.reject()
    assert not flow.view.is_editing and flow.row()["status"] == "REJECTED"
    kept = draft_row(flow.paths, UID)
    assert kept is not None and kept["stem"] == "ტექსტი, რომელიც არ უნდა დაიკარგოს"
    assert flow.editing.draft_bar.isVisible()
    # a status decision is not a content conflict: the draft can still be restored and saved
    flow.editing.draft_bar.restore_button.click()
    assert flow.view.is_editing and flow.editing.editor.save()
    row = flow.row()
    assert row["status"] == "REJECTED" and row["revision_count"] == 2  # editing never un-rejects


# ------------------------------------------------------------------ reopen and start


def test_reopening_asks_first_and_returns_the_question_to_review(flow: Flow) -> None:
    flow.open()
    assert flow.wf.approve()
    flow.answer(ask_reopen_note=None)
    assert not flow.wf.reopen() and flow.row()["status"] == "APPROVED"
    asked: list[ReviewStatus] = []
    flow.wf.ask_reopen_note = lambda status: asked.append(status) or "ახალი წყარო"  # type: ignore[method-assign]
    assert flow.wf.reopen()
    assert asked == [ReviewStatus.APPROVED]
    row = flow.row()
    assert row["status"] == "IN_REVIEW" and row["approved_revision_id"] is None
    assert events_of(flow.paths, UID)[-1]["note"] == "ახალი წყარო"
    assert flow.actions() == {ReviewAction.APPROVE, ReviewAction.REJECT}
    assert "ხელახლა გაიხსნა" in flow.notice()[1]


def test_a_rejected_question_can_be_reopened_and_then_approved(flow: Flow) -> None:
    flow.open()
    flow.answer(ask_reject_reason="მიზეზი", ask_reopen_note="")
    assert flow.wf.reject() and flow.wf.reopen() and flow.wf.approve()
    assert flow.row()["status"] == "APPROVED"
    assert [e["action"] for e in events_of(flow.paths, UID)] == ["IMPORTED", "REJECT", "REOPEN", "APPROVE"]


def test_start_review_moves_a_pending_question_without_changing_text(flow: Flow) -> None:
    flow.open()
    flow.wf.bar.start_button.click()
    row = flow.row()
    assert row["status"] == "IN_REVIEW" and row["revision_count"] == 1
    assert ReviewAction.START_REVIEW not in flow.actions()
    assert "განხილვა დაიწყო" in flow.notice()[1]


# ------------------------------------------------------------------ conflicts and errors


@pytest.mark.parametrize("action", ["reject", "reopen", "start"])
def test_a_conflict_reloads_the_screen_and_changes_nothing(flow: Flow, action: str) -> None:
    from bank import approve

    if action == "reopen":
        approve(flow.paths, UID)
    flow.open()
    flow.answer(ask_reject_reason="მიზეზი", ask_reopen_note="")
    simulate_edit(flow.paths, UID, stem="სხვის მიერ შეცვლილი")
    before = snapshot(flow.paths)
    call = {"reject": flow.wf.reject, "reopen": flow.wf.reopen, "start": flow.wf.start_review}[action]
    assert not call()
    kind, text = flow.notice()
    assert kind == "ErrorNotice" and "კითხვა სხვაგან შეიცვალა" in text
    assert snapshot(flow.paths) == before and flow.view.detail_view.detail.revision_no == 2


def test_a_storage_failure_is_explained_and_nothing_changes(flow: Flow, monkeypatch) -> None:
    flow.open()
    monkeypatch.setattr(
        flow.review,
        "approve",
        lambda *a, **k: (_ for _ in ()).throw(StorageFailure(StorageProblem.DISK_FULL, "full")),
    )
    before = snapshot(flow.paths)
    assert not flow.wf.approve()
    kind, text = flow.notice()
    assert kind == "ErrorNotice" and "დისკზე ადგილი არ არის" in text and "არაფერი შეცვლილა" in text
    assert snapshot(flow.paths) == before and not flow.saved


def test_every_message_is_georgian(flow: Flow) -> None:
    """No raw status or action codes leak into the interface."""
    texts: list[str] = []
    flow.open()
    flow.answer(ask_reject_reason="მიზეზი", ask_reopen_note="", ask_save_and_approve=True)
    for step in (flow.wf.start_review, flow.wf.approve, flow.wf.approve, flow.wf.reject, flow.wf.reopen):
        step()
        texts.append(flow.notice()[1])
    for text in texts:
        words = re.findall(r"[A-Za-z]{3,}", text.replace(UID, ""))
        assert not words, (text, words)


# ------------------------------------------------------------------ restore from the history


def make_history(flow: Flow) -> None:
    flow.open()
    d = flow.catalog.detail_by_uid(UID)
    for stem in ("მეორე რედაქცია", "მესამე რედაქცია"):
        d = flow.catalog.detail_by_uid(UID)
        content = type(d.content)(stem, d.content.options, d.content.correct, d.content.explanation)
        flow.edits.save(d.id, d.lock_version, content)
    flow.view.reload_current()


def test_the_history_restores_an_earlier_revision_through_an_explicit_action(flow: Flow) -> None:
    make_history(flow)
    original = revisions_of(flow.paths, UID)[0]["stem"]
    assert flow.view.show_history()
    history = flow.editing.history
    assert not history.restore_button.isEnabled()  # the newest = current revision
    history.select_revision_number(1)
    assert history.restore_button.isEnabled()
    shown: list[tuple] = []
    flow.wf.ask_restore = lambda src, new, *, approved, draft: (  # type: ignore[method-assign]
        shown.append((src, new, approved, draft)) or RestoreChoice.RESTORE
    )
    history.restore_button.click()
    assert shown == [(1, 4, False, None)]
    revisions = revisions_of(flow.paths, UID)
    assert [r["revision_no"] for r in revisions] == [1, 2, 3, 4] and revisions[3]["origin"] == "RESTORE"
    assert (
        revisions[3]["stem"] == original and revisions[3]["restored_from_revision_id"] == revisions[0]["id"]
    )
    assert flow.view.detail_view.detail.revision_no == 4
    assert history.revisions[0].revision_no == 4 and history.list.currentRow() == 0  # the list reloaded
    assert "აღდგენა (რედაქცია 1-დან)" in history.list.item(0).text()
    kind, text = flow.notice()
    assert kind == "SuccessNotice" and "ახალი რედაქცია 4" in text
    assert flow.editing.mode is EditMode.HISTORY  # the reviewer stays where they were


def test_cancelling_the_restore_changes_nothing(flow: Flow) -> None:
    make_history(flow)
    assert flow.view.show_history()
    flow.editing.history.select_revision_number(1)
    flow.answer(ask_restore=RestoreChoice.CANCEL)
    before = snapshot(flow.paths)
    flow.editing.history.restore_button.click()
    assert snapshot(flow.paths) == before


def test_restore_is_not_offered_for_the_current_text_or_an_identical_revision(flow: Flow) -> None:
    flow.open()
    d = flow.catalog.detail_by_uid(UID)
    other = type(d.content)("სხვა", d.content.options, d.content.correct, d.content.explanation)
    flow.edits.save(d.id, d.lock_version, other)
    d = flow.catalog.detail_by_uid(UID)
    flow.edits.save(
        d.id,
        d.lock_version,
        type(d.content)(
            revisions_of(flow.paths, UID)[0]["stem"],
            d.content.options,
            d.content.correct,
            d.content.explanation,
        ),
    )  # revision 3 has revision 1's text again
    flow.view.reload_current()
    assert flow.view.show_history()
    history = flow.editing.history
    history.select_revision_number(1)
    assert not history.restore_button.isEnabled()
    assert history.restore_button.toolTip() == strings.HI_RESTORE_SAME_TEXT
    history.select_revision_number(2)
    assert history.restore_button.isEnabled()


def test_restoring_on_an_approved_question_warns_and_revokes_the_approval(flow: Flow) -> None:
    make_history(flow)
    assert flow.wf.approve()
    assert flow.view.show_history()
    flow.editing.history.select_revision_number(2)
    seen: list[bool] = []
    flow.wf.ask_restore = lambda src, new, *, approved, draft: (  # type: ignore[method-assign]
        seen.append(approved) or RestoreChoice.RESTORE
    )
    flow.editing.history.restore_button.click()
    assert seen == [True]
    row = flow.row()
    assert row["status"] == "IN_REVIEW" and row["approved_revision_id"] is None
    assert "დამტკიცება გაუქმდა" in flow.notice()[1]
    assert flow.editing.mode is EditMode.HISTORY and not flow.wf.bar.isVisible()


@pytest.mark.parametrize(
    ("choice", "draft_survives", "discards"),
    [(RestoreChoice.KEEP_DRAFT, True, 0), (RestoreChoice.DISCARD_DRAFT, False, 1)],
)
def test_restore_makes_the_reviewer_decide_what_happens_to_a_draft(
    flow: Flow, choice, draft_survives: bool, discards: int
) -> None:
    make_history(flow)
    d = flow.catalog.detail_by_uid(UID)
    mine = type(d.content)("ჩემი მონახაზი", d.content.options, d.content.correct, "e")
    flow.drafts.save_draft(d.id, d.revision_id, d.lock_version, mine, REVIEWER)
    assert flow.view.show_history()
    flow.editing.history.select_revision_number(1)
    asked: list[object] = []
    flow.wf.ask_restore = lambda src, new, *, approved, draft: asked.append(draft) or choice  # type: ignore[method-assign]
    flow.editing.history.restore_button.click()
    assert asked[0] is not None and asked[0][1] == REVIEWER  # the dialog was told about the draft
    assert len(revisions_of(flow.paths, UID)) == 4
    assert (draft_row(flow.paths, UID) is not None) is draft_survives
    assert flow.drafts.count() == (1 if draft_survives else 0)
    import sqlite3

    conn = sqlite3.connect(flow.paths.database_file)
    try:
        assert conn.execute("SELECT count(*) FROM draft_discards").fetchone()[0] == discards
    finally:
        conn.close()


def test_a_restore_conflict_is_reported_and_the_history_reloads(flow: Flow) -> None:
    make_history(flow)
    assert flow.view.show_history()
    flow.editing.history.select_revision_number(1)
    simulate_edit(flow.paths, UID, stem="სხვა ფანჯრიდან")
    flow.answer(ask_restore=RestoreChoice.RESTORE)
    before = snapshot(flow.paths)
    flow.editing.history.restore_button.click()
    assert snapshot(flow.paths) == before
    kind, text = flow.notice()
    assert kind == "ErrorNotice" and "კითხვა სხვაგან შეიცვალა" in text
    assert flow.editing.history.revisions[0].revision_no == 4  # the history shows the other change


# ------------------------------------------------------------------ keyboard


def press(widget, key, modifiers=Qt.KeyboardModifier.ControlModifier) -> None:
    QTest.keyClick(widget, key, modifiers)


def test_the_shortcuts_drive_the_workflow(flow: Flow) -> None:
    flow.open()
    flow.view.detail_view.setFocus()
    flow.answer(ask_reject_reason="მიზეზი", ask_reopen_note="")
    press(flow.view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)  # approve and next
    assert flow.row()["status"] == "APPROVED" and flow.view.current_uid == "ALI-0004"
    press(
        flow.view, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier
    )
    assert flow.row("ALI-0004")["status"] == "APPROVED" and flow.view.current_uid == "ALI-0004"  # stays
    press(flow.view, Qt.Key.Key_O, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert flow.row("ALI-0004")["status"] == "IN_REVIEW"
    press(flow.view, Qt.Key.Key_R, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
    assert flow.row("ALI-0004")["status"] == "REJECTED"


def test_ctrl_enter_works_inside_the_editor_and_never_inserts_text(flow: Flow) -> None:
    edit_stem(flow, "ტექსტი კლავიატურით დასამტკიცებლად")
    flow.answer(ask_save_and_approve=True)
    editor = flow.editing.editor
    press(editor.stem_edit, Qt.Key.Key_Return)
    assert flow.row()["status"] == "APPROVED" and flow.view.current_uid == "ALI-0004"
    assert "\n" not in revisions_of(flow.paths, UID)[-1]["stem"]


def test_restore_shortcut_acts_on_the_selected_revision_in_the_history(flow: Flow) -> None:
    make_history(flow)
    assert flow.view.show_history()
    flow.editing.history.select_revision_number(1)
    flow.answer(ask_restore=RestoreChoice.RESTORE)
    press(
        flow.editing.history,
        Qt.Key.Key_B,
        Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier,
    )
    assert len(revisions_of(flow.paths, UID)) == 4


def test_no_two_shortcuts_share_a_key_sequence(flow: Flow) -> None:
    """The new workflow keys must not collide with the existing ones (same widget tree).

    ``Esc`` is the one deliberate repeat: it clears the search box (bound to that box only) and
    cancels the editor (bound to the editor and its children) — never active at the same time.
    """
    seen: dict[str, object] = {}
    for shortcut in flow.view.findChildren(QShortcut):
        key = shortcut.key().toString()
        if not key or key == "Esc":
            continue
        assert key not in seen, f"{key} is bound twice"
        seen[key] = shortcut.context()
    expected = {
        "Ctrl+E", "Ctrl+H", "Ctrl+S", "Ctrl+1", "Ctrl+5",
        "Ctrl+Return", "Ctrl+Enter", "Ctrl+Shift+Return", "Ctrl+Shift+Enter",
        "Ctrl+Shift+R", "Ctrl+Shift+O", "Ctrl+Shift+B",
    }  # fmt: skip
    assert expected <= set(seen)


# ------------------------------------------------------------------ other invariants


def test_an_untouched_question_is_never_changed_by_decisions_on_its_neighbours(flow: Flow) -> None:
    flow.open()
    others = {uid: question_row(flow.paths, uid) for uid in ("ALI-0001", "ALI-0002", "VENOUS-0001")}
    flow.answer(ask_reject_reason="მიზეზი")
    flow.wf.approve_and_next()
    assert flow.wf.reject()
    for uid, row in others.items():
        assert question_row(flow.paths, uid) == row
    assert bank_counts(flow.paths).total_with(ReviewStatus.APPROVED) == 1
    assert bank_counts(flow.paths).total_with(ReviewStatus.REJECTED) == 1


def test_key_events_with_georgian_text_still_reach_the_editor(flow: Flow) -> None:
    """The new Ctrl+Enter bindings must not swallow ordinary typing."""
    flow.open()
    assert flow.view.start_editing()
    editor = flow.editing.editor
    editor.stem_edit.clear()
    editor.stem_edit.setFocus()
    for char in "ახალი ტექსტი":
        for kind in (QEvent.Type.KeyPress, QEvent.Type.KeyRelease):
            QApplication.sendEvent(
                editor.stem_edit, QKeyEvent(kind, Qt.Key.Key_unknown, Qt.KeyboardModifier.NoModifier, char)
            )
    assert editor.stem_edit.toPlainText() == "ახალი ტექსტი"
