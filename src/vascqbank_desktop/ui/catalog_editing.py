"""Editing behaviour of the catalog page: edit mode, history, draft recovery.

:class:`EditingController` is owned by :class:`~vascqbank_desktop.ui.catalog_view.CatalogView`
(which stays unaware of the details) and keeps the detail area in exactly one of three modes:

* ``READ``    — the read-only detail view (the default);
* ``EDIT``    — the editor; leaving it always flushes the draft first;
* ``HISTORY`` — the read-only revision history.

It also owns draft recovery: a bar above a question that has an unfinished draft (restore /
compare / discard — never automatic), a banner while any draft exists, and the list of all
drafts. A draft is never removed because its question is filtered out or because the program
was restarted; only an explicit save of that text or an explicit discard removes it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut
from PySide6.QtWidgets import QHBoxLayout, QMessageBox, QPushButton, QStackedWidget, QVBoxLayout

from vascqbank_desktop.domain.catalog import QuestionDetail
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.textdiff import compare_contents
from vascqbank_desktop.services.draft_service import DiscardReason, DraftService
from vascqbank_desktop.services.edit_errors import ReviewerRequired, StorageFailure
from vascqbank_desktop.services.edit_service import EditService, SaveResult
from vascqbank_desktop.services.review_service import ReviewService
from vascqbank_desktop.services.settings_service import ReviewerProfile
from vascqbank_desktop.ui import strings
from vascqbank_desktop.ui.autosave import DEFAULT_DELAY_MS, DEFAULT_RETRY_MS
from vascqbank_desktop.ui.diff_view import CompareDialog
from vascqbank_desktop.ui.draft_widgets import DraftBanner, DraftBar, DraftListDialog
from vascqbank_desktop.ui.keys import key
from vascqbank_desktop.ui.question_editor_view import EditSession, QuestionEditorView
from vascqbank_desktop.ui.revision_history_view import RevisionHistoryView

if TYPE_CHECKING:
    from vascqbank_desktop.ui.catalog_view import CatalogView

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class EditingServices:
    edit: EditService
    drafts: DraftService
    profile: ReviewerProfile
    review: ReviewService | None = None  # approve / reject / reopen / restore (Phase 5)


class EditMode(StrEnum):
    READ = "READ"
    EDIT = "EDIT"
    HISTORY = "HISTORY"


class EditingController:
    def __init__(
        self,
        view: CatalogView,
        services: EditingServices,
        *,
        autosave_delay_ms: int = DEFAULT_DELAY_MS,
        autosave_retry_ms: int = DEFAULT_RETRY_MS,
    ) -> None:
        self.view = view
        self.edit_service = services.edit
        self.drafts = services.drafts
        self.profile = services.profile
        self.mode = EditMode.READ

        self.editor = QuestionEditorView(
            services.edit,
            services.drafts,
            services.profile,
            view,
            autosave_delay_ms=autosave_delay_ms,
            autosave_retry_ms=autosave_retry_ms,
        )
        self.history = RevisionHistoryView(view.service, view)
        self.draft_bar = DraftBar(view)
        self.banner = DraftBanner(view)
        self.edit_button = QPushButton(strings.EDIT_BUTTON)
        self.edit_button.setObjectName("NavButton")
        self.edit_button.setToolTip(strings.EDIT_BUTTON_TOOLTIP)
        self.history_button = QPushButton(strings.HISTORY_BUTTON)
        self.history_button.setObjectName("NavButton")
        self.history_button.setToolTip(strings.HISTORY_BUTTON_TOOLTIP)
        self.stack: QStackedWidget | None = None
        self._dialogs: list[object] = []  # comparison / list windows kept alive while open

        self.edit_button.clicked.connect(self.start_editing)
        self.history_button.clicked.connect(self.show_history)
        self.history.backRequested.connect(self.close_history)
        self.editor.finished.connect(self._on_editor_finished)
        self.editor.saved.connect(self._on_saved)
        self.editor.draftChanged.connect(self.refresh_drafts_state)
        self.editor.compareRequested.connect(self._compare_conflict)
        self.editor.reviewerRequired.connect(view.reviewerRequired)
        self.draft_bar.restoreRequested.connect(self.start_editing)
        self.draft_bar.compareRequested.connect(self._compare_draft_of_current)
        self.draft_bar.discardRequested.connect(self._discard_draft_of_current)
        self.banner.openListRequested.connect(self.open_draft_list)

        shortcut = QShortcut(key("Ctrl+E"), view)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(self.start_editing)
        history_shortcut = QShortcut(key("Ctrl+H"), view)
        history_shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        history_shortcut.activated.connect(self.show_history)
        self._shortcuts = [shortcut, history_shortcut]

    # ------------------------------------------------------------------ installation
    def install(self, bar: QHBoxLayout, column: QVBoxLayout, stack: QStackedWidget) -> None:
        """Add the buttons, the draft bar and the editor/history pages to the detail area."""
        self.stack = stack
        bar.addWidget(self.edit_button)
        bar.addWidget(self.history_button)
        column.insertWidget(1, self.draft_bar)
        stack.addWidget(self.editor)
        stack.addWidget(self.history)
        self.update_buttons()

    # ------------------------------------------------------------------ small helpers
    def update_buttons(self) -> None:
        has_question = self.view.current_question_id is not None
        reading = self.mode is EditMode.READ
        self.edit_button.setEnabled(has_question and reading)
        self.history_button.setEnabled(has_question and reading)

    def _show(self, widget) -> None:  # noqa: ANN001
        if self.stack is not None:
            self.stack.setCurrentWidget(widget)

    def _set_mode(self, mode: EditMode) -> None:
        editing_before = self.mode is EditMode.EDIT
        self.mode = mode
        self.update_buttons()
        self.view.refresh_workflow()
        if (mode is EditMode.EDIT) != editing_before:
            self.view.editingChanged.emit(mode is EditMode.EDIT)

    # ------------------------------------------------------------------ entering edit mode
    def start_editing(self) -> bool:
        """Open the editor on the displayed question (restoring its draft, if it has one)."""
        view = self.view
        question_id = view.current_question_id
        if self.mode is EditMode.EDIT:
            return True
        if question_id is None:
            return False
        if self.mode is EditMode.HISTORY:
            self.close_history()
        if not self.profile.name:
            view.show_notice(strings.ED_NEED_REVIEWER, error=True)
            view.reviewerRequired.emit()
            return False
        detail = view.service.detail(question_id)  # fresh: another window may have saved meanwhile
        if detail is None:
            return False
        draft = self.drafts.load_draft(question_id)
        session = self._make_session(detail, draft)
        if session is None:
            return False
        if view.detail_view.detail != detail:
            view.detail_view.show_question(detail)
        self.draft_bar.hide()
        self.editor.begin(session)
        self._show(self.editor)
        self._set_mode(EditMode.EDIT)
        return True

    def _make_session(self, detail: QuestionDetail, draft) -> EditSession | None:  # noqa: ANN001
        if draft is None:
            return EditSession(
                question_id=detail.id,
                uid=detail.uid,
                subject_name=detail.subject_name_ka,
                base_revision_id=detail.revision_id,
                base_revision_no=detail.revision_no,
                base_lock_version=detail.lock_version,
                base_content=detail.content,
                status=detail.status,
                current_revision_no=detail.revision_no,
                initial_content=detail.content,
            )
        base_lock = draft.base_lock_version
        if draft.base_revision_id == detail.revision_id:
            base_content, base_no = detail.content, detail.revision_no
            # Same text as when the draft began: only a status decision (approve, reject, reopen)
            # can have moved the lock version since, and that must not look like a conflict.
            base_lock = detail.lock_version
        else:  # the draft started from an older revision: measure it against that one
            revision = self.view.service.revision(draft.base_revision_id)
            if revision is None:
                return None
            base_content, base_no = revision.content, revision.info.revision_no
        return EditSession(
            question_id=detail.id,
            uid=detail.uid,
            subject_name=detail.subject_name_ka,
            base_revision_id=draft.base_revision_id,
            base_revision_no=base_no,
            base_lock_version=base_lock,
            base_content=base_content,
            status=detail.status,
            current_revision_no=detail.revision_no,
            initial_content=draft.content,
            initial_note=draft.note or "",
            restored_draft=draft,
        )

    # ------------------------------------------------------------------ leaving
    def leave_for_navigation(self) -> bool:
        """Called before another question is shown: flush and leave edit mode / history.

        ``False`` means "stay": the draft could not be written and the reviewer chose to keep
        the text, so navigation must not proceed.
        """
        if self.mode is EditMode.EDIT:
            if not self.editor.leave():
                return False
            self._show(self.view.detail_view)
            self._set_mode(EditMode.READ)
            self.refresh_drafts_state()
        elif self.mode is EditMode.HISTORY:
            self.close_history()
        return True

    def flush_draft(self) -> bool:
        """Persist any pending draft now, staying in edit mode (filter and page changes)."""
        return self.editor.flush_draft() if self.mode is EditMode.EDIT else True

    def _on_editor_finished(self) -> None:
        """Cancel / "keep as draft": back to the read-only view of the saved text."""
        self._show(self.view.detail_view)
        self._set_mode(EditMode.READ)
        self.view.reload_current()
        self.refresh_drafts_state()

    def _on_saved(self, result: SaveResult) -> None:
        self.view.reload_current()  # the read-only view underneath shows the new revision at once
        self.view.after_saved(result)
        self.refresh_drafts_state()

    def prepare_shutdown(self) -> bool:
        ok = self.editor.shutdown()
        if ok and self.mode is EditMode.EDIT:
            self._show(self.view.detail_view)
            self._set_mode(EditMode.READ)
        return ok

    # ------------------------------------------------------------------ history
    def show_history(self) -> bool:
        view = self.view
        if self.mode is not EditMode.READ or view.current_question_id is None or view.current_uid is None:
            return False
        self.history.show_history(view.current_question_id, view.current_uid)
        self.draft_bar.hide()
        self._show(self.history)
        self._set_mode(EditMode.HISTORY)
        return True

    def close_history(self) -> None:
        if self.mode is EditMode.HISTORY:
            self._show(self.view.detail_view)
            self._set_mode(EditMode.READ)
            self.refresh_draft_bar()

    # ------------------------------------------------------------------ drafts: bar, banner, list
    def refresh_drafts_state(self) -> None:
        """Re-read which questions have drafts (banner, list markers)."""
        try:
            ids = self.drafts.draft_question_ids()
        except Exception:  # a failed read must not break navigation
            log.exception("Could not read the list of drafts")
            return
        self.view.model.set_draft_ids(ids)
        self.banner.set_count(len(ids))
        self.view.draftsChanged.emit(len(ids))
        if self.mode is EditMode.READ:
            self.refresh_draft_bar()

    def refresh_draft_bar(self) -> None:
        """Offer the draft of the displayed question (read mode only). Never acts by itself."""
        question_id = self.view.current_question_id
        if self.mode is not EditMode.READ or question_id is None:
            self.draft_bar.hide()
            return
        info = self.drafts.draft_info(question_id)
        if info is None:
            self.draft_bar.hide()
            return
        outdated = (info.base_revision_no, info.current_revision_no) if info.outdated else None
        self.draft_bar.show_draft(info.reviewer_name, info.saved_at, outdated)

    def confirm_discard_draft(self, uid: str) -> bool:
        answer = QMessageBox.question(
            self.view,
            strings.DR_DISCARD_TITLE,
            strings.DR_DISCARD_TEXT.format(uid=uid),
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        return answer == QMessageBox.StandardButton.Yes

    def discard_draft(self, question_id: int, uid: str) -> bool:
        """Intentionally discard a draft (after confirmation); its text is archived."""
        if not self.confirm_discard_draft(uid):
            return False
        try:
            self.drafts.discard_draft(question_id, self.profile.name or "—", DiscardReason.DISCARDED)
        except (StorageFailure, ReviewerRequired) as exc:
            self.view.show_notice(strings.ED_STATE_ERROR.format(detail=str(exc)), error=True)
            return False
        self.refresh_drafts_state()
        return True

    def _discard_draft_of_current(self) -> None:
        if self.view.current_question_id is not None and self.view.current_uid is not None:
            self.discard_draft(self.view.current_question_id, self.view.current_uid)

    def open_draft_list(self) -> DraftListDialog:
        dialog = DraftListDialog(self.view)
        dialog.set_drafts(self.drafts.list_drafts())
        dialog.openRequested.connect(lambda qid: self._open_draft_from_list(dialog, qid))
        dialog.compareRequested.connect(self._compare_draft)
        dialog.discardRequested.connect(lambda qid: self._discard_from_list(dialog, qid))
        dialog.finished.connect(lambda _r: self._forget(dialog))
        self._dialogs.append(dialog)
        dialog.open()
        return dialog

    def _open_draft_from_list(self, dialog: DraftListDialog, question_id: int) -> None:
        location = self.view.service.locate_id(question_id)
        if location is None:
            return
        dialog.accept()
        if self.view.show_question_by_location(location):  # the draft is offered by the bar ...
            self.start_editing()  # ... and "open" means restore it in the editor

    def _discard_from_list(self, dialog: DraftListDialog, question_id: int) -> None:
        row = next((r for r in dialog.rows if r.question_id == question_id), None)
        if row is not None and self.discard_draft(question_id, row.uid):
            dialog.set_drafts(self.drafts.list_drafts())

    # ------------------------------------------------------------------ comparisons
    def _forget(self, dialog: object) -> None:
        if dialog in self._dialogs:
            self._dialogs.remove(dialog)

    def _show_comparison(self, title: str, old: QuestionContent, new: QuestionContent) -> CompareDialog:
        dialog = CompareDialog(title, compare_contents(old, new), self.view)
        dialog.finished.connect(lambda _r: self._forget(dialog))
        self._dialogs.append(dialog)
        dialog.open()
        return dialog

    def _compare_conflict(self, session: EditSession, mine: QuestionContent) -> None:
        current = self.view.service.detail(session.question_id)
        if current is not None:
            self._show_comparison(
                f"{session.uid}: {strings.DIFF_OLD_TITLE.format(title=strings.ED_CONFLICT_MINE)} → "
                f"{strings.HI_CURRENT} ({current.revision_no})",
                current.content,
                mine,
            )

    def _compare_draft(self, question_id: int) -> None:
        draft = self.drafts.load_draft(question_id)
        current = self.view.service.detail(question_id)
        if draft is not None and current is not None:
            self._show_comparison(
                f"{current.uid}: {strings.DR_COMPARE} ({current.revision_no})", current.content, draft.content
            )

    def _compare_draft_of_current(self) -> None:
        if self.view.current_question_id is not None:
            self._compare_draft(self.view.current_question_id)
