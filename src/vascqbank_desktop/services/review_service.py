"""Review decisions: approve, reject, reopen, start review, and restore an earlier revision.

Every method opens its own short-lived SQLite connection (never shared between threads), takes
the process-wide write lock, requires the reviewer name chosen on this computer, and delegates
to a repository function that runs **one** ``BEGIN IMMEDIATE`` transaction. A success is
reported only after ``COMMIT``; every failure leaves the database exactly as it was.

The caller passes the ``lock_version`` of the question *as the reviewer saw it*; if the
question changed since, :class:`~vascqbank_desktop.services.edit_errors.EditConflict` is raised
instead of approving or rejecting text the reviewer never saw.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from dataclasses import dataclass

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import edit_repository as edit_repo
from vascqbank_desktop.db import review_repository as repo
from vascqbank_desktop.db.connection import make_durable, open_database
from vascqbank_desktop.db.migrations import utc_now_iso
from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.editing import (
    MAX_NOTE_LENGTH,
    FieldProblem,
    ProblemKind,
    normalize_note,
    validate_content,
)
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.workflow import ReviewAction
from vascqbank_desktop.services.edit_errors import (
    STORAGE_ERRORS,
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
from vascqbank_desktop.services.settings_service import ReviewerNotSetError, ReviewerProfile

log = logging.getLogger(__name__)

RESTORE = "RESTORE"


@dataclass(frozen=True, slots=True)
class ReviewResult:
    """What the database confirmed after a review action or a restore."""

    action: str  # START_REVIEW / APPROVE / REJECT / REOPEN / RESTORE
    changed: bool  # False: nothing was written (already approved / already in review / same text)
    question_id: int
    status_before: ReviewStatus
    status_after: ReviewStatus
    revision_id: int  # the current revision after the call
    revision_no: int
    lock_version: int
    approved_revision_id: int | None
    approved_at: str | None
    approval_revoked: bool
    revoked_revision_no: int | None
    modified_at: str
    draft_cleared: bool
    actor: str
    created_revision: bool = False  # RESTORE: a new revision was appended
    restored_from_revision_no: int | None = None


class ReviewService:
    def __init__(self, paths: AppPaths, profile: ReviewerProfile) -> None:
        self._paths = paths
        self._profile = profile
        self._write_lock = threading.Lock()

    # -- helpers ------------------------------------------------------------------
    def _actor(self) -> str:
        try:
            return self._profile.require_name()
        except ReviewerNotSetError as exc:
            raise ReviewerRequired(str(exc)) from exc

    def _run(self, question_id: int, what: str, work):  # noqa: ANN001, ANN202
        """Run *work(conn)* on a fresh durable connection, translating failures."""
        with self._write_lock:
            try:
                conn = make_durable(open_database(self._paths.database_file))  # per call: never shared
                try:
                    return work(conn)
                finally:
                    conn.close()
            except (repo.LockConflict, edit_repo.LockConflict) as exc:
                raise EditConflict(
                    question_id,
                    exc.expected_lock_version,
                    exc.current_lock_version,
                    exc.current_revision_no,
                    exc.current_status,
                ) from exc
            except (repo.QuestionMissing, edit_repo.QuestionMissing) as exc:
                raise EditTargetMissing(question_id) from exc
            except edit_repo.DraftPending as exc:
                raise UnresolvedDraft(question_id, exc.saved_at, exc.reviewer_name) from exc
            except repo.ContentInvalid as exc:
                raise ValidationFailed(exc.problems) from exc
            except repo.InvalidTransitionError as exc:
                raise InvalidTransition(question_id, exc.action.value, exc.status) from exc
            except STORAGE_ERRORS as exc:
                log.warning("%s of question %s failed: %s", what, question_id, exc)
                raise StorageFailure.from_exception(exc) from exc

    def _apply(
        self, action: ReviewAction, question_id: int, expected_lock_version: int, note: str | None
    ) -> ReviewResult:
        actor = self._actor()
        outcome = self._run(
            question_id,
            action.value,
            lambda conn: repo.apply_action(
                conn,
                question_id=question_id,
                expected_lock_version=expected_lock_version,
                action=action,
                actor=actor,
                note=note,
                now=utc_now_iso(),
            ),
        )
        return ReviewResult(
            action=action.value,
            changed=outcome.changed,
            question_id=outcome.question_id,
            status_before=outcome.status_before,
            status_after=outcome.status_after,
            revision_id=outcome.revision_id,
            revision_no=outcome.revision_no,
            lock_version=outcome.lock_version,
            approved_revision_id=outcome.approved_revision_id,
            approved_at=outcome.approved_at,
            approval_revoked=outcome.approval_revoked,
            revoked_revision_no=outcome.revoked_revision_no,
            modified_at=outcome.modified_at,
            draft_cleared=outcome.draft_cleared,
            actor=actor,
        )

    @staticmethod
    def _checked_note(note: str | None) -> str | None:
        cleaned = normalize_note(note)
        if cleaned is not None and len(cleaned) > MAX_NOTE_LENGTH:
            raise ValidationFailed([FieldProblem(None, ProblemKind.NOTE_TOO_LONG)])
        return cleaned

    # -- decisions --------------------------------------------------------------------
    def start_review(self, question_id: int, expected_lock_version: int) -> ReviewResult:
        """``PENDING_REVIEW → IN_REVIEW`` without changing the text (already in review: nothing)."""
        return self._apply(ReviewAction.START_REVIEW, question_id, expected_lock_version, None)

    def approve(self, question_id: int, expected_lock_version: int, note: str | None = None) -> ReviewResult:
        """Approve the **current saved revision** (never a draft).

        Refused — nothing written — if the reviewer name is missing, the question changed since
        it was loaded (:class:`EditConflict`), the saved text does not validate
        (:class:`ValidationFailed`), the status does not allow it (:class:`InvalidTransition`)
        or an unsaved draft exists (:class:`UnresolvedDraft`). Approving an already approved
        revision succeeds with ``changed=False`` and records nothing.
        """
        return self._apply(ReviewAction.APPROVE, question_id, expected_lock_version, self._checked_note(note))

    def reject(self, question_id: int, expected_lock_version: int, reason: str) -> ReviewResult:
        """Reject with a mandatory *reason*; content and revisions are kept, any approval is cleared."""
        cleaned = self._checked_note(reason)
        if cleaned is None:
            raise ReasonRequired("a rejection needs a written reason")
        return self._apply(ReviewAction.REJECT, question_id, expected_lock_version, cleaned)

    def reopen(self, question_id: int, expected_lock_version: int, note: str | None = None) -> ReviewResult:
        """``APPROVED``/``REJECTED`` → ``IN_REVIEW``; the approval is cleared, history is kept."""
        return self._apply(ReviewAction.REOPEN, question_id, expected_lock_version, self._checked_note(note))

    # -- restore ------------------------------------------------------------------------
    def restore_revision(
        self,
        question_id: int,
        expected_lock_version: int,
        source_revision_id: int,
        note: str | None = None,
        *,
        keep_draft: bool = False,
    ) -> ReviewResult:
        """Append the text of an earlier revision as a NEW revision (``origin = RESTORE``).

        Old revisions are never changed. The usual status rules apply (an approved question
        loses its approval); restoring text identical to the current revision creates nothing.
        An unsaved draft is preserved, never deleted: unless *keep_draft* is true the call is
        refused with :class:`UnresolvedDraft` so the reviewer resolves it explicitly first.
        """
        actor = self._actor()
        cleaned_note = self._checked_note(note)

        def work(conn: sqlite3.Connection) -> tuple[edit_repo.EditOutcome, int]:
            source = conn.execute(
                "SELECT question_id, revision_no, stem, option_a, option_b, option_c, option_d, option_e,"
                " correct_letter, explanation FROM question_revisions WHERE id = ?",
                (source_revision_id,),
            ).fetchone()
            if source is None or int(source[0]) != question_id:
                raise RestoreSourceInvalid(
                    f"revision {source_revision_id} is not a revision of {question_id}"
                )
            content = QuestionContent(
                source[2], tuple(source[3:8]), AnswerLetter.from_letter(source[8]), source[9]
            )
            problems = validate_content(content, cleaned_note)
            if problems:
                raise ValidationFailed(problems)
            source_no = int(source[1])
            event_note = f"restored from revision {source_no}"
            if cleaned_note:
                event_note += f"; {cleaned_note}"
            outcome = edit_repo.append_edit(
                conn,
                question_id=question_id,
                expected_lock_version=expected_lock_version,
                content=content,
                author=actor,
                note=cleaned_note,
                event_note=event_note,
                now=utc_now_iso(),
                origin=RESTORE,
                restored_from_revision_id=source_revision_id,
                event_action=RESTORE,
                refuse_pending_draft=not keep_draft,
            )
            return outcome, source_no

        outcome, source_no = self._run(question_id, RESTORE, work)
        approved = self._approved_pointer(question_id, outcome)
        return ReviewResult(
            action=RESTORE,
            changed=outcome.created,
            question_id=outcome.question_id,
            status_before=outcome.status_before,
            status_after=outcome.status_after,
            revision_id=outcome.revision_id,
            revision_no=outcome.revision_no,
            lock_version=outcome.lock_version,
            approved_revision_id=approved[0],
            approved_at=approved[1],
            approval_revoked=outcome.approval_revoked,
            revoked_revision_no=outcome.revoked_revision_no,
            modified_at=outcome.modified_at,
            draft_cleared=outcome.draft_cleared,
            actor=actor,
            created_revision=outcome.created,
            restored_from_revision_no=source_no,
        )

    def _approved_pointer(
        self, question_id: int, outcome: edit_repo.EditOutcome
    ) -> tuple[int | None, str | None]:
        """The approval pointers after a restore (an approved question that was not changed keeps them)."""
        if outcome.status_after is not ReviewStatus.APPROVED:
            return None, None
        conn = open_database(self._paths.database_file)
        try:
            row = conn.execute(
                "SELECT approved_revision_id, approved_at FROM questions WHERE id = ?", (question_id,)
            ).fetchone()
        finally:
            conn.close()
        return (row[0], row[1]) if row else (None, None)
