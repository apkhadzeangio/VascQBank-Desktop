"""The authoritative write path for review decisions: approve, reject, reopen, start review.

:func:`apply_action` is the only place that changes a question's *status* without changing its
content. Like :func:`~vascqbank_desktop.db.edit_repository.append_edit` it is **one**
``BEGIN IMMEDIATE … COMMIT`` transaction:

1. read the question and its current revision;
2. refuse if ``lock_version`` differs from the one the reviewer saw (:class:`LockConflict`);
3. an action that would change nothing (approving an already approved revision, starting a
   review that is already running) commits nothing — no duplicate event;
4. check the transition is allowed (:class:`InvalidTransition`);
5. for ``APPROVE`` only: the authoritative text must validate (:class:`ContentInvalid`) and the
   question must have no unresolved draft (:class:`DraftPending`) — a draft is working text the
   reviewer has not saved, so approving "around" it could approve the wrong thing;
6. update ``questions`` (status, approval pointers, ``lock_version + 1``, ``modified_at``);
   approval binds ``approved_revision_id`` to exactly ``current_revision_id``; rejecting or
   reopening clears the pointers (the schema allows them only on an approved question) — the
   historical approval stays in the append-only events;
7. append the review event and commit. Any failure rolls everything back.

Content, revisions, the original imported text and identity are never touched.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from vascqbank_desktop.db.edit_repository import (
    CURRENT_SQL,
    DraftPending,
    LockConflict,
    QuestionMissing,
    pending_draft,
    row_content,
)
from vascqbank_desktop.domain.editing import FieldProblem, validate_content
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.workflow import ReviewAction, is_repeat, next_status, revokes_approval

__all__ = [
    "ContentInvalid",
    "DraftPending",
    "InvalidTransitionError",
    "LockConflict",
    "QuestionMissing",
    "ReviewOutcome",
    "apply_action",
]


class InvalidTransitionError(Exception):
    def __init__(self, question_id: int, action: ReviewAction, status: ReviewStatus) -> None:
        self.question_id = question_id
        self.action = action
        self.status = status
        super().__init__(f"{action.value} is not allowed from {status.value}")


class ContentInvalid(Exception):
    def __init__(self, problems: list[FieldProblem]) -> None:
        self.problems = problems
        super().__init__(f"{len(problems)} validation problem(s)")


@dataclass(frozen=True, slots=True)
class ReviewOutcome:
    action: ReviewAction
    changed: bool  # False: nothing was written (the question was already there)
    question_id: int
    status_before: ReviewStatus
    status_after: ReviewStatus
    revision_id: int  # the current revision (never changed by these actions)
    revision_no: int
    lock_version: int
    approved_revision_id: int | None
    approved_at: str | None
    approval_revoked: bool
    revoked_revision_no: int | None
    modified_at: str
    draft_cleared: bool  # an identical (pointless) draft was removed with the approval


def _revision_no(conn: sqlite3.Connection, revision_id: int | None) -> int | None:
    if revision_id is None:
        return None
    row = conn.execute("SELECT revision_no FROM question_revisions WHERE id = ?", (revision_id,)).fetchone()
    return int(row[0]) if row else None


def apply_action(
    conn: sqlite3.Connection,
    *,
    question_id: int,
    expected_lock_version: int,
    action: ReviewAction,
    actor: str,
    note: str | None,
    now: str,
) -> ReviewOutcome:
    """Apply one review action atomically (see the module docstring)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(CURRENT_SQL, (question_id,)).fetchone()
        if row is None:
            raise QuestionMissing(question_id)
        status_before = ReviewStatus(row[0])
        lock_version, current_revision_id = int(row[1]), int(row[2])
        approved_revision_id = row[3]
        modified_at, revision_no = row[4], int(row[5])
        if lock_version != expected_lock_version:
            raise LockConflict(
                question_id,
                expected_lock_version,
                lock_version,
                current_revision_id,
                revision_no,
                status_before,
            )

        if is_repeat(status_before, action) and (
            action is not ReviewAction.APPROVE or approved_revision_id == current_revision_id
        ):
            approved_at_before = conn.execute(
                "SELECT approved_at FROM questions WHERE id = ?", (question_id,)
            ).fetchone()[0]
            conn.execute("COMMIT")
            return ReviewOutcome(
                action=action,
                changed=False,
                question_id=question_id,
                status_before=status_before,
                status_after=status_before,
                revision_id=current_revision_id,
                revision_no=revision_no,
                lock_version=lock_version,
                approved_revision_id=approved_revision_id,
                approved_at=approved_at_before,
                approval_revoked=False,
                revoked_revision_no=None,
                modified_at=modified_at,
                draft_cleared=False,
            )

        status_after = next_status(status_before, action)
        if status_after is None:
            raise InvalidTransitionError(question_id, action, status_before)

        draft_cleared = False
        if action is ReviewAction.APPROVE:
            content = row_content(row)
            problems = validate_content(content, None)
            if problems:
                raise ContentInvalid(problems)
            pending = pending_draft(conn, question_id, content)
            if pending is not None:
                raise DraftPending(question_id, pending[0], pending[1])
            # a draft that exists but is not pending merely repeats the saved text: it carries
            # nothing, so it goes away with the approval
            if conn.execute("SELECT 1 FROM drafts WHERE question_id = ?", (question_id,)).fetchone():
                conn.execute("DELETE FROM drafts WHERE question_id = ?", (question_id,))
                draft_cleared = True

        revokes = revokes_approval(status_before, action)
        revoked_no = _revision_no(conn, approved_revision_id) if revokes else None
        approving = action is ReviewAction.APPROVE
        new_approved_revision = current_revision_id if approving else None
        new_approved_at = now if approving else None
        updated = conn.execute(
            """
            UPDATE questions SET status = ?, approved_revision_id = ?, approved_at = ?,
                lock_version = lock_version + 1, modified_at = ?
            WHERE id = ? AND lock_version = ?
            """,
            (
                status_after.value,
                new_approved_revision,
                new_approved_at,
                now,
                question_id,
                expected_lock_version,
            ),
        )
        if updated.rowcount != 1:  # cannot happen inside BEGIN IMMEDIATE; never ignore it if it does
            raise sqlite3.IntegrityError("question changed during the review action")
        conn.execute(
            """
            INSERT INTO review_events (question_id, action, from_status, to_status, revision_id, actor, note,
                                       created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                action.value,
                status_before.value,
                status_after.value,
                current_revision_id,
                actor,
                note,
                now,
            ),
        )
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return ReviewOutcome(
        action=action,
        changed=True,
        question_id=question_id,
        status_before=status_before,
        status_after=status_after,
        revision_id=current_revision_id,
        revision_no=revision_no,
        lock_version=expected_lock_version + 1,
        approved_revision_id=new_approved_revision,
        approved_at=new_approved_at,
        approval_revoked=revokes,
        revoked_revision_no=revoked_no,
        modified_at=now,
        draft_cleared=draft_cleared,
    )
