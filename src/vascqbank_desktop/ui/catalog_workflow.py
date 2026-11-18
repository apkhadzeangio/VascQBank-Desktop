"""The editorial workflow of the catalog page: approve, reject, reopen, start review, restore.

:class:`WorkflowController` is owned by :class:`~vascqbank_desktop.ui.catalog_view.CatalogView`
next to the editing controller. It never touches the database: every decision goes through
:class:`~vascqbank_desktop.services.review_service.ReviewService`, which writes in one
transaction and reports success only after ``COMMIT``. The controller

* offers exactly the actions the displayed question's status allows (``WorkflowBar``);
* always acts on the text **the reviewer is looking at** (its ``lock_version``), so a question
  that changed elsewhere produces a conflict instead of approving unseen text;
* writes unsaved editor text safely first — an explicit save in edit mode, a draft flush
  otherwise — and never navigates away when that fails;
* shows the new state at once (detail, list, counters) and a visible success or error message;
* for *Approve and next*: remembers the next question of the list **before** acting (so the
  order stays predictable when the approved question leaves an active status filter), and moves
  there only after the approval is confirmed.
"""

from __future__ import annotations

import logging
from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QMessageBox, QVBoxLayout

from vascqbank_desktop.domain.catalog import QuestionDetail
from vascqbank_desktop.domain.editing import same_content
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.workflow import ReviewAction, allowed_actions
from vascqbank_desktop.services.draft_service import DiscardReason
from vascqbank_desktop.services.edit_errors import (
    EditConflict,
    EditTargetMissing,
    InvalidTransition,
    ReasonRequired,
    RestoreSourceInvalid,
    ReviewerRequired,
    StorageFailure,
    UnresolvedDraft,
    ValidationFailed,
)
from vascqbank_desktop.services.review_service import ReviewResult
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.formatting import format_clock
from vascqbank_desktop.ui.keys import key
from vascqbank_desktop.ui.question_editor_view import problem_text, storage_text
from vascqbank_desktop.ui.workflow_widgets import ReasonDialog, WorkflowBar

if TYPE_CHECKING:
    from vascqbank_desktop.ui.catalog_editing import EditingServices
    from vascqbank_desktop.ui.catalog_view import CatalogView

log = logging.getLogger(__name__)


class RestoreChoice(StrEnum):
    CANCEL = "CANCEL"
    RESTORE = "RESTORE"
    KEEP_DRAFT = "KEEP_DRAFT"  # restore and leave the unsaved draft where it is
    DISCARD_DRAFT = "DISCARD_DRAFT"  # archive the draft, then restore


class WorkflowController:
    def __init__(self, view: CatalogView, services: EditingServices) -> None:
        assert services.review is not None
        self.view = view
        self.review = services.review
        self.drafts = services.drafts
        self.profile = services.profile
        self.bar = WorkflowBar(view)
        self._shortcuts: list[QShortcut] = []

        self.bar.approveNextRequested.connect(self.approve_and_next)
        self.bar.approveRequested.connect(self.approve)
        self.bar.rejectRequested.connect(self.reject)
        self.bar.reopenRequested.connect(self.reopen)
        self.bar.startRequested.connect(self.start_review)
        self._bind(("Ctrl+Return", "Ctrl+Enter"), self.approve_and_next)
        self._bind(("Ctrl+Shift+Return", "Ctrl+Shift+Enter"), self.approve)
        self._bind(("Ctrl+Shift+R",), self.reject)
        self._bind(("Ctrl+Shift+O",), self.reopen)
        self._bind(("Ctrl+Shift+B",), self.restore_selected)
        self._bind(("Ctrl+Shift+I",), self.start_review)

    def _bind(self, sequences: tuple[str, ...], handler) -> None:  # noqa: ANN001
        for sequence in sequences:
            shortcut = QShortcut(key(sequence), self.view)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(handler)
            self._shortcuts.append(shortcut)

    # ------------------------------------------------------------------ installation / state
    def install(self, column: QVBoxLayout) -> None:
        """Put the action bar under the navigation row and connect the history's restore button."""
        column.insertWidget(1, self.bar)
        editing = self.view.editing
        if editing is not None:
            editing.history.restoreRequested.connect(self.restore)
        self.refresh()

    def refresh(self) -> None:
        """Show the actions of the displayed question (hidden while the history is open)."""
        detail = self.view.detail_view.detail
        editing = self.view.editing
        in_history = editing is not None and editing.mode.value == "HISTORY"
        self.bar.setVisible(detail is not None and not in_history)
        self.bar.set_status(detail.status if detail is not None else None)

    def available(self, action: ReviewAction) -> bool:
        detail = self.view.detail_view.detail
        return detail is not None and action in allowed_actions(detail.status)

    # ------------------------------------------------------------------ dialogs (overridable in tests)
    def ask_reject_reason(self, *, approved: bool) -> str | None:
        dialog = ReasonDialog(
            strings.WF_REJECT_TITLE,
            strings.WF_REJECT_INTRO,
            confirm_text=strings.WF_REJECT_CONFIRM,
            required=True,
            warning=strings.WF_REJECT_APPROVED_WARNING if approved else "",
            destructive=True,
            parent=self.view,
        )
        return dialog.text() if dialog.exec() else None

    def ask_reopen_note(self, status: ReviewStatus) -> str | None:
        """``None`` = cancelled; ``""`` = confirmed without a note."""
        intro = (
            strings.WF_REOPEN_INTRO_APPROVED
            if status is ReviewStatus.APPROVED
            else strings.WF_REOPEN_INTRO_REJECTED
        )
        dialog = ReasonDialog(
            strings.WF_REOPEN_TITLE,
            intro,
            confirm_text=strings.WF_REOPEN_CONFIRM,
            required=False,
            parent=self.view,
        )
        return dialog.text() if dialog.exec() else None

    def ask_save_and_approve(self) -> bool:
        box = QMessageBox(
            QMessageBox.Icon.Question,
            strings.WF_SAVE_APPROVE_TITLE,
            strings.WF_SAVE_APPROVE_TEXT,
            parent=self.view,
        )
        yes = box.addButton(strings.WF_SAVE_APPROVE_BUTTON, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(strings.WF_CANCEL, QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is yes

    def ask_open_draft(self, error: UnresolvedDraft) -> bool:
        """A draft blocks the approval: open it in the editor (``True``) or cancel."""
        box = QMessageBox(
            QMessageBox.Icon.Warning,
            strings.WF_DRAFT_TITLE,
            strings.WF_DRAFT_TEXT.format(reviewer=error.reviewer_name, time=format_clock(error.saved_at)),
            parent=self.view,
        )
        open_button = box.addButton(strings.WF_DRAFT_OPEN, QMessageBox.ButtonRole.AcceptRole)
        box.addButton(strings.WF_DRAFT_CANCEL, QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return box.clickedButton() is open_button

    def ask_restore(
        self, source_no: int, new_no: int, *, approved: bool, draft: tuple[str, str] | None
    ) -> RestoreChoice:
        """Confirm a restore; with a draft present the reviewer must choose what happens to it."""
        text = strings.WF_RESTORE_TEXT.format(source=source_no, new=new_no)
        if approved:
            text += "\n\n" + strings.WF_RESTORE_APPROVED_WARNING
        if draft is not None:
            text += "\n\n" + strings.WF_RESTORE_DRAFT.format(reviewer=draft[1], time=format_clock(draft[0]))
        box = QMessageBox(QMessageBox.Icon.Question, strings.WF_RESTORE_TITLE, text, parent=self.view)
        buttons: dict[object, RestoreChoice] = {}
        if draft is None:
            buttons[box.addButton(strings.WF_RESTORE_CONFIRM, QMessageBox.ButtonRole.AcceptRole)] = (
                RestoreChoice.RESTORE
            )
        else:
            buttons[box.addButton(strings.WF_RESTORE_KEEP_DRAFT, QMessageBox.ButtonRole.AcceptRole)] = (
                RestoreChoice.KEEP_DRAFT
            )
            buttons[
                box.addButton(strings.WF_RESTORE_DISCARD_DRAFT, QMessageBox.ButtonRole.DestructiveRole)
            ] = RestoreChoice.DISCARD_DRAFT
        box.addButton(strings.WF_CANCEL, QMessageBox.ButtonRole.RejectRole)
        box.exec()
        return buttons.get(box.clickedButton(), RestoreChoice.CANCEL)

    # ------------------------------------------------------------------ plumbing
    def _notice(self, text: str, *, error: bool = False, success: bool = False) -> None:
        self.view.show_notice(text, error=error, success=success)

    def _reviewer_ok(self) -> bool:
        if self.profile.name:
            return True
        self._notice(strings.ED_NEED_REVIEWER, error=True)
        self.view.reviewerRequired.emit()
        return False

    def _displayed(self, action: ReviewAction) -> QuestionDetail | None:
        """The question the reviewer is looking at, if *action* is allowed on it."""
        detail = self.view.detail_view.detail
        if detail is None:
            return None
        if action not in allowed_actions(detail.status):
            self._notice(
                strings.WF_ERR_PREFIX + strings.WF_ERR_INVALID.format(status=detail.status.label_ka),
                error=True,
            )
            return None
        return detail

    def _leave_for_action(self) -> bool:
        """Leave the editor or history before a status change; the draft is flushed first.

        ``False`` means the unsaved text could not be written and the reviewer kept it: the
        action must not go ahead.
        """
        editing = self.view.editing
        if editing is None:
            return True
        if editing.mode.value == "HISTORY":
            editing.close_history()
            return True
        return editing.leave_for_navigation()

    def _call(self, detail: QuestionDetail, call) -> ReviewResult | None:  # noqa: ANN001
        """Run one service call; show the right Georgian message when it fails."""
        try:
            return call()
        except EditConflict as exc:
            self.view.reload_current()
            self.view.refresh()
            self._notice(
                strings.WF_ERR_PREFIX
                + strings.WF_ERR_CONFLICT.format(
                    current=exc.current_revision_no, status=exc.current_status.label_ka
                ),
                error=True,
            )
        except UnresolvedDraft as exc:
            if self.ask_open_draft(exc):
                self.view.start_editing()
        except ValidationFailed as exc:
            details = "; ".join(problem_text(p) for p in exc.problems)
            self._notice(
                strings.WF_ERR_PREFIX + strings.WF_ERR_VALIDATION.format(details=details), error=True
            )
        except InvalidTransition as exc:
            self.view.reload_current()
            status = ReviewStatus(exc.status).label_ka
            self._notice(strings.WF_ERR_PREFIX + strings.WF_ERR_INVALID.format(status=status), error=True)
        except ReasonRequired:
            self._notice(strings.WF_ERR_PREFIX + strings.WF_ERR_REASON, error=True)
        except ReviewerRequired:
            self._notice(strings.ED_NEED_REVIEWER, error=True)
            self.view.reviewerRequired.emit()
        except EditTargetMissing:
            self._notice(strings.WF_ERR_PREFIX + strings.WF_ERR_MISSING, error=True)
        except RestoreSourceInvalid:
            self._notice(strings.WF_ERR_PREFIX + strings.WF_ERR_NO_SOURCE, error=True)
        except StorageFailure as exc:
            self._notice(
                strings.WF_ERR_PREFIX + strings.WF_ERR_STORAGE.format(detail=storage_text(exc)), error=True
            )
        return None

    def _committed(self, result: ReviewResult) -> None:
        """A decision is in the database: show it everywhere at once (detail, list, counters)."""
        view = self.view
        view.reload_current()
        view.after_saved(result)  # re-runs the filter and tells the window to refresh the counters
        self.refresh()
        if view.editing is not None:
            view.editing.refresh_drafts_state()

    # ------------------------------------------------------------------ approve
    def approve(self) -> bool:
        return self._approve(go_next=False)

    def approve_and_next(self) -> bool:
        return self._approve(go_next=True)

    def _approve(self, *, go_next: bool) -> bool:
        view = self.view
        if view.detail_view.detail is None or not self._reviewer_ok():
            return False
        if self._displayed(ReviewAction.APPROVE) is None:
            return False
        current_id = view.current_question_id
        # The question after this one in the list as it is NOW: if approving makes the question
        # leave an active status filter, this is still where the reviewer expects to continue.
        target = view.model.results.next_id(current_id) if go_next else None
        editing = view.editing
        if editing is not None and editing.mode.value == "EDIT":
            editor = editing.editor
            if editor.differs_from_saved() and (not self.ask_save_and_approve() or not editor.save()):
                return False  # declined, invalid, conflicting or failed: the text stays in the editor
        if not self._leave_for_action():
            return False
        detail = view.detail_view.detail  # after an explicit save this is the NEW revision
        if detail is None or detail.id != current_id:
            return False
        result = self._call(detail, lambda: self.review.approve(detail.id, detail.lock_version))
        if result is None:
            return False
        self._committed(result)
        if result.changed:
            message = strings.WF_OK_APPROVED.format(number=result.revision_no)
        else:
            message = strings.WF_OK_ALREADY_APPROVED
        self._notice(f"{detail.uid} — {message}", success=True)
        if go_next:
            self._go_to(target, detail.uid, message)
        return True

    def _go_to(self, target: int | None, uid: str, message: str) -> None:
        """Move on after a confirmed approval; stay (with a message) if there is nowhere to go."""
        view = self.view
        if target is None:
            self._notice(f"{uid} — {message}. {strings.WF_LAST_QUESTION}", success=True)
            return
        if not view.open_question(target):
            self._notice(strings.WF_ERR_PREFIX + strings.WF_NEXT_NOT_POSSIBLE, error=True)
            return
        self._notice(f"{uid} — {message}", success=True)

    # ------------------------------------------------------------------ reject / reopen / start
    def reject(self) -> bool:
        detail = self._displayed(ReviewAction.REJECT)
        if detail is None or not self._reviewer_ok():
            return False
        reason = self.ask_reject_reason(approved=detail.status is ReviewStatus.APPROVED)
        if reason is None:
            return False
        if not self._leave_for_action():
            return False
        result = self._call(detail, lambda: self.review.reject(detail.id, detail.lock_version, reason))
        if result is None:
            return False
        self._committed(result)
        message = (
            strings.WF_OK_REJECTED_REVOKED.format(number=result.revoked_revision_no)
            if result.approval_revoked and result.revoked_revision_no
            else strings.WF_OK_REJECTED
        )
        self._notice(f"{detail.uid} — {message}", success=True)
        return True

    def reopen(self) -> bool:
        detail = self._displayed(ReviewAction.REOPEN)
        if detail is None or not self._reviewer_ok():
            return False
        note = self.ask_reopen_note(detail.status)
        if note is None:
            return False
        if not self._leave_for_action():
            return False
        result = self._call(detail, lambda: self.review.reopen(detail.id, detail.lock_version, note))
        if result is None:
            return False
        self._committed(result)
        self._notice(f"{detail.uid} — {strings.WF_OK_REOPENED}", success=True)
        return True

    def start_review(self) -> bool:
        detail = self._displayed(ReviewAction.START_REVIEW)
        if detail is None or not self._reviewer_ok() or not self._leave_for_action():
            return False
        result = self._call(detail, lambda: self.review.start_review(detail.id, detail.lock_version))
        if result is None:
            return False
        self._committed(result)
        message = strings.WF_OK_STARTED if result.changed else strings.WF_OK_ALREADY_STARTED
        self._notice(f"{detail.uid} — {message}", success=True)
        return True

    # ------------------------------------------------------------------ restore
    def restore_selected(self) -> bool:
        """The shortcut: restore the revision selected in the open history."""
        editing = self.view.editing
        if editing is None or editing.mode.value != "HISTORY":
            return False
        selected = editing.history.selected_revision
        if selected is None or not editing.history.restore_button.isEnabled():
            return False
        return self.restore(selected.info.id, selected.info.revision_no)

    def restore(self, revision_id: int, revision_no: int) -> bool:
        """Restore an earlier revision as a NEW revision (explicit, confirmed, never silent)."""
        view = self.view
        detail = view.detail_view.detail
        if detail is None or not self._reviewer_ok():
            return False
        draft = self.drafts.load_draft(detail.id)
        pending = (
            (draft.saved_at, draft.reviewer_name)
            if draft is not None and not same_content(draft.content, detail.content)
            else None
        )
        choice = self.ask_restore(
            revision_no,
            detail.revision_no + 1,
            approved=detail.status is ReviewStatus.APPROVED,
            draft=pending,
        )
        if choice is RestoreChoice.CANCEL:
            return False
        if choice is RestoreChoice.DISCARD_DRAFT:
            try:
                self.drafts.discard_draft(detail.id, self.profile.name or "—", DiscardReason.DISCARDED)
            except (StorageFailure, ReviewerRequired) as exc:
                text = storage_text(exc) if isinstance(exc, StorageFailure) else strings.ED_NEED_REVIEWER
                self._notice(strings.WF_ERR_PREFIX + strings.WF_ERR_STORAGE.format(detail=text), error=True)
                return False
        keep = choice is RestoreChoice.KEEP_DRAFT
        result = self._call(
            detail,
            lambda: self.review.restore_revision(
                detail.id, detail.lock_version, revision_id, keep_draft=keep
            ),
        )
        if result is None:
            self._reload_history()
            return False
        self._committed(result)
        self._reload_history()
        if not result.created_revision:
            self._notice(f"{detail.uid} — {strings.WF_OK_RESTORE_SAME}", success=True)
            return True
        template = strings.WF_OK_RESTORED_REVOKED if result.approval_revoked else strings.WF_OK_RESTORED
        self._notice(
            f"{detail.uid} — {template.format(source=revision_no, number=result.revision_no)}", success=True
        )
        return True

    def _reload_history(self) -> None:
        editing = self.view.editing
        if editing is not None and editing.mode.value == "HISTORY":
            editing.history.reload()
