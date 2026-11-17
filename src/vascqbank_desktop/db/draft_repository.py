"""SQL for editing drafts (``drafts`` and ``draft_discards``, migration 3).

Every write is one explicit ``BEGIN IMMEDIATE … COMMIT`` transaction: when a function here
returns normally the draft is committed; on any error nothing changed (the transaction is
rolled back and the exception propagates). Nothing here touches ``questions``,
``question_revisions`` or ``review_events``.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.editing import same_content
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import ReviewStatus

_COLUMNS = (
    "id, question_id, base_revision_id, base_lock_version, stem, option_a, option_b, option_c, option_d,"
    " option_e, correct_letter, explanation, note, reviewer_name, write_count, created_at, saved_at"
)


@dataclass(frozen=True, slots=True)
class DraftRecord:
    id: int
    question_id: int
    base_revision_id: int
    base_lock_version: int
    content: QuestionContent
    note: str | None
    reviewer_name: str
    write_count: int
    created_at: str
    saved_at: str


@dataclass(frozen=True, slots=True)
class DraftListRow:
    """A draft as listed for recovery: identity and age, never the (possibly long) text."""

    question_id: int
    uid: str
    subject_code: str
    question_status: ReviewStatus
    base_revision_id: int
    base_revision_no: int
    current_revision_id: int
    current_revision_no: int
    reviewer_name: str
    saved_at: str

    @property
    def outdated(self) -> bool:
        """The question received a newer saved revision after this draft was started."""
        return self.base_revision_id != self.current_revision_id


def _record(row: sqlite3.Row) -> DraftRecord:
    return DraftRecord(
        id=int(row[0]),
        question_id=int(row[1]),
        base_revision_id=int(row[2]),
        base_lock_version=int(row[3]),
        content=QuestionContent(
            stem=row[4],
            options=tuple(row[5:10]),
            correct=AnswerLetter.from_letter(row[10]),
            explanation=row[11],
        ),
        note=row[12],
        reviewer_name=row[13],
        write_count=int(row[14]),
        created_at=row[15],
        saved_at=row[16],
    )


def _transaction(conn: sqlite3.Connection) -> None:
    conn.execute("BEGIN IMMEDIATE")


def _rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        conn.execute("ROLLBACK")


def upsert_draft(
    conn: sqlite3.Connection,
    *,
    question_id: int,
    base_revision_id: int,
    base_lock_version: int,
    content: QuestionContent,
    note: str | None,
    reviewer_name: str,
    now: str,
) -> DraftRecord:
    """Insert or replace the draft of one question, atomically; return what was committed."""
    _transaction(conn)
    try:
        conn.execute(
            """
            INSERT INTO drafts (question_id, base_revision_id, base_lock_version, stem, option_a, option_b,
                                option_c, option_d, option_e, correct_letter, explanation, note,
                                reviewer_name, write_count, created_at, saved_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
            ON CONFLICT (question_id) DO UPDATE SET
                base_revision_id = excluded.base_revision_id,
                base_lock_version = excluded.base_lock_version,
                stem = excluded.stem, option_a = excluded.option_a, option_b = excluded.option_b,
                option_c = excluded.option_c, option_d = excluded.option_d, option_e = excluded.option_e,
                correct_letter = excluded.correct_letter, explanation = excluded.explanation,
                note = excluded.note, reviewer_name = excluded.reviewer_name,
                write_count = drafts.write_count + 1, saved_at = excluded.saved_at
            """,
            (
                question_id,
                base_revision_id,
                base_lock_version,
                content.stem,
                *content.options,
                content.correct.value,
                content.explanation,
                note,
                reviewer_name,
                now,
                now,
            ),
        )
        row = conn.execute(f"SELECT {_COLUMNS} FROM drafts WHERE question_id = ?", (question_id,)).fetchone()
        conn.execute("COMMIT")
    except BaseException:
        _rollback(conn)
        raise
    return _record(row)


def get_draft(conn: sqlite3.Connection, question_id: int) -> DraftRecord | None:
    row = conn.execute(f"SELECT {_COLUMNS} FROM drafts WHERE question_id = ?", (question_id,)).fetchone()
    return _record(row) if row is not None else None


def draft_question_ids(conn: sqlite3.Connection) -> set[int]:
    return {int(r[0]) for r in conn.execute("SELECT question_id FROM drafts")}


def count_drafts(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT count(*) FROM drafts").fetchone()[0])


def list_drafts(conn: sqlite3.Connection, question_id: int | None = None) -> list[DraftListRow]:
    """Unfinished drafts, newest first, with how far their question has moved on
    (all of them, or only the one of *question_id*)."""
    where = "" if question_id is None else "WHERE d.question_id = ?"
    rows = conn.execute(
        f"""
        SELECT d.question_id, q.question_uid, q.subject_code, q.status,
               d.base_revision_id, base.revision_no, q.current_revision_id, cur.revision_no,
               d.reviewer_name, d.saved_at
        FROM drafts d
        JOIN questions q ON q.id = d.question_id
        JOIN question_revisions base ON base.id = d.base_revision_id
        JOIN question_revisions cur ON cur.id = q.current_revision_id
        {where}
        ORDER BY d.saved_at DESC, d.id DESC
        """,
        () if question_id is None else (question_id,),
    ).fetchall()
    return [
        DraftListRow(
            question_id=int(r[0]),
            uid=r[1],
            subject_code=r[2],
            question_status=ReviewStatus(r[3]),
            base_revision_id=int(r[4]),
            base_revision_no=int(r[5]),
            current_revision_id=int(r[6]),
            current_revision_no=int(r[7]),
            reviewer_name=r[8],
            saved_at=r[9],
        )
        for r in rows
    ]


def delete_draft(
    conn: sqlite3.Connection,
    question_id: int,
    *,
    archive_reason: str | None,
    discarded_by: str,
    now: str,
) -> bool:
    """Remove a draft; with *archive_reason* its full text is first copied to ``draft_discards``
    **in the same transaction**, so a discard can never lose text half-way. Returns whether a
    draft existed."""
    _transaction(conn)
    try:
        existing = conn.execute("SELECT 1 FROM drafts WHERE question_id = ?", (question_id,)).fetchone()
        if existing is None:
            conn.execute("COMMIT")
            return False
        if archive_reason is not None:
            conn.execute(
                """
                INSERT INTO draft_discards (question_id, base_revision_id, base_lock_version, stem, option_a,
                    option_b, option_c, option_d, option_e, correct_letter, explanation, note,
                    reviewer_name, draft_saved_at, discarded_at, discarded_by, reason)
                SELECT question_id, base_revision_id, base_lock_version, stem, option_a, option_b, option_c,
                       option_d, option_e, correct_letter, explanation, note, reviewer_name, saved_at,
                       ?, ?, ?
                FROM drafts WHERE question_id = ?
                """,
                (now, discarded_by, archive_reason, question_id),
            )
        conn.execute("DELETE FROM drafts WHERE question_id = ?", (question_id,))
        conn.execute("COMMIT")
    except BaseException:
        _rollback(conn)
        raise
    return True


def count_discards(conn: sqlite3.Connection, question_id: int | None = None) -> int:
    if question_id is None:
        return int(conn.execute("SELECT count(*) FROM draft_discards").fetchone()[0])
    row = conn.execute("SELECT count(*) FROM draft_discards WHERE question_id = ?", (question_id,)).fetchone()
    return int(row[0])


def delete_draft_if_identical(conn: sqlite3.Connection, question_id: int, content: QuestionContent) -> bool:
    """Delete the draft only if it holds exactly *content* (after normalisation): such a draft
    carries no information, so removing it loses nothing. Returns whether it was deleted."""
    _transaction(conn)
    try:
        row = conn.execute(f"SELECT {_COLUMNS} FROM drafts WHERE question_id = ?", (question_id,)).fetchone()
        deleted = row is not None and same_content(_record(row).content, content)
        if deleted:
            conn.execute("DELETE FROM drafts WHERE question_id = ?", (question_id,))
        conn.execute("COMMIT")
    except BaseException:
        _rollback(conn)
        raise
    return deleted
