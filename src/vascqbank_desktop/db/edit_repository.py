"""The authoritative write path for editing: appending a revision.

:func:`append_edit` is the only place that adds an ``EDIT`` revision. It runs as **one**
``BEGIN IMMEDIATE … COMMIT`` transaction:

1. read the question and its current revision;
2. refuse if ``lock_version`` differs from the one the editor started from
   (:class:`LockConflict`) — nothing is changed;
3. if the normalised content equals the current revision, create nothing;
4. otherwise append the revision (next contiguous number, origin ``EDIT``), point
   ``current_revision_id`` at it, apply the status rule (``status_after_edit``; an approved
   question loses its approval because it was bound to the previous revision), bump
   ``lock_version`` and ``modified_at``;
5. append the ``EDITED`` review event;
6. remove the draft of this question **if it holds exactly the text just saved**;
7. commit. Any failure rolls everything back (including the draft deletion).

Identity, provenance and ``original_content_json`` are never touched — the database triggers
forbid it — and existing revisions and events are append-only.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.editing import normalize_content, same_content
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import (
    ReviewStatus,
    edit_revokes_approval,
    status_after_edit,
)


class QuestionMissing(LookupError):
    def __init__(self, question_id: int) -> None:
        self.question_id = question_id
        super().__init__(f"question {question_id} does not exist")


class LockConflict(Exception):
    """The question was saved by someone else after the editor loaded it."""

    def __init__(
        self,
        question_id: int,
        expected_lock_version: int,
        current_lock_version: int,
        current_revision_id: int,
        current_revision_no: int,
        current_status: ReviewStatus,
    ) -> None:
        self.question_id = question_id
        self.expected_lock_version = expected_lock_version
        self.current_lock_version = current_lock_version
        self.current_revision_id = current_revision_id
        self.current_revision_no = current_revision_no
        self.current_status = current_status
        super().__init__(
            f"question {question_id} changed (lock version {expected_lock_version} → {current_lock_version})"
        )


@dataclass(frozen=True, slots=True)
class EditOutcome:
    created: bool  # a new revision was appended
    question_id: int
    revision_id: int  # current revision after the call
    revision_no: int
    lock_version: int
    status_before: ReviewStatus
    status_after: ReviewStatus
    approval_revoked: bool
    revoked_revision_no: int | None
    modified_at: str
    draft_cleared: bool


_CURRENT_SQL = """
SELECT q.status, q.lock_version, q.current_revision_id, q.approved_revision_id, q.modified_at,
       r.revision_no, r.stem, r.option_a, r.option_b, r.option_c, r.option_d, r.option_e,
       r.correct_letter, r.explanation
FROM questions q JOIN question_revisions r ON r.id = q.current_revision_id
WHERE q.id = ?
"""


def _clear_matching_draft(conn: sqlite3.Connection, question_id: int, saved: QuestionContent) -> bool:
    row = conn.execute(
        "SELECT stem, option_a, option_b, option_c, option_d, option_e, correct_letter, explanation"
        " FROM drafts WHERE question_id = ?",
        (question_id,),
    ).fetchone()
    if row is None:
        return False
    draft = QuestionContent(row[0], tuple(row[1:6]), AnswerLetter.from_letter(row[6]), row[7])
    if not same_content(draft, saved):
        return False  # a different, unsaved text: never delete it as a side effect
    conn.execute("DELETE FROM drafts WHERE question_id = ?", (question_id,))
    return True


def append_edit(
    conn: sqlite3.Connection,
    *,
    question_id: int,
    expected_lock_version: int,
    content: QuestionContent,
    author: str,
    note: str | None,
    event_note: str | None,
    now: str,
) -> EditOutcome:
    """Save *content* as the next revision of a question (see the module docstring)."""
    content = normalize_content(content)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(_CURRENT_SQL, (question_id,)).fetchone()
        if row is None:
            raise QuestionMissing(question_id)
        status_before = ReviewStatus(row[0])
        lock_version, current_revision_id, approved_revision_id = int(row[1]), int(row[2]), row[3]
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

        current = QuestionContent(row[6], tuple(row[7:12]), AnswerLetter.from_letter(row[12]), row[13])
        if same_content(current, content):
            cleared = _clear_matching_draft(conn, question_id, content)
            conn.execute("COMMIT")
            return EditOutcome(
                created=False,
                question_id=question_id,
                revision_id=current_revision_id,
                revision_no=revision_no,
                lock_version=lock_version,
                status_before=status_before,
                status_after=status_before,
                approval_revoked=False,
                revoked_revision_no=None,
                modified_at=modified_at,
                draft_cleared=cleared,
            )

        next_no = int(
            conn.execute(
                "SELECT COALESCE(MAX(revision_no), 0) + 1 FROM question_revisions WHERE question_id = ?",
                (question_id,),
            ).fetchone()[0]
        )
        cursor = conn.execute(
            """
            INSERT INTO question_revisions (question_id, revision_no, stem, option_a, option_b, option_c,
                option_d, option_e, correct_letter, explanation, origin, author, note, content_hash,
                created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EDIT', ?, ?, ?, ?)
            """,
            (
                question_id,
                next_no,
                content.stem,
                *content.options,
                content.correct.value,
                content.explanation,
                author,
                note,
                content.content_hash(),
                now,
            ),
        )
        new_revision_id = int(cursor.lastrowid or 0)

        status_after = status_after_edit(status_before)
        revokes = edit_revokes_approval(status_before)
        revoked_no: int | None = None
        if revokes and approved_revision_id is not None:
            revoked_row = conn.execute(
                "SELECT revision_no FROM question_revisions WHERE id = ?", (approved_revision_id,)
            ).fetchone()
            revoked_no = int(revoked_row[0]) if revoked_row else None

        updated = conn.execute(
            """
            UPDATE questions SET current_revision_id = ?, status = ?, lock_version = lock_version + 1,
                modified_at = ?,
                approved_revision_id = CASE WHEN ? THEN NULL ELSE approved_revision_id END,
                approved_at = CASE WHEN ? THEN NULL ELSE approved_at END
            WHERE id = ? AND lock_version = ?
            """,
            (new_revision_id, status_after.value, now, revokes, revokes, question_id, expected_lock_version),
        )
        if updated.rowcount != 1:  # cannot happen inside BEGIN IMMEDIATE; never ignore it if it does
            raise sqlite3.IntegrityError("question changed during the save")

        revoked_text = f"approval of revision {revoked_no} revoked" if revokes else None
        notes = [n for n in (event_note, revoked_text) if n]
        conn.execute(
            """
            INSERT INTO review_events (question_id, action, from_status, to_status, revision_id, actor, note,
                                       created_at)
            VALUES (?, 'EDITED', ?, ?, ?, ?, ?, ?)
            """,
            (
                question_id,
                status_before.value,
                status_after.value,
                new_revision_id,
                author,
                "; ".join(notes) or None,
                now,
            ),
        )
        cleared = _clear_matching_draft(conn, question_id, content)
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return EditOutcome(
        created=True,
        question_id=question_id,
        revision_id=new_revision_id,
        revision_no=next_no,
        lock_version=expected_lock_version + 1,
        status_before=status_before,
        status_after=status_after,
        approval_revoked=revokes,
        revoked_revision_no=revoked_no,
        modified_at=now,
        draft_cleared=cleared,
    )
