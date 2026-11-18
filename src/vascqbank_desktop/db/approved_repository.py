"""Read-only access to the approved text — the only source an exporter may use.

Everything here reads ``questions.approved_revision_id`` — **never** ``current_revision_id`` and
never the ``drafts`` table — and only for questions whose status is ``APPROVED``. A question
that is pending, in review, rejected, or approved-then-edited therefore cannot appear, and the
text returned is always the exact revision that was approved, not a later or earlier one.

All functions only run ``SELECT`` statements (the module is covered by the read-only test that
scans catalog/approved repositories for write keywords).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import ReviewStatus


@dataclass(frozen=True, slots=True)
class ApprovedQuestion:
    """One exportable question: its immutable ID and the exact approved revision."""

    question_id: int  # internal row id
    uid: str  # immutable question ID, e.g. ALI-0042
    subject_code: str
    subject_name_ka: str
    subject_order: int
    source_number: int
    revision_id: int  # = questions.approved_revision_id
    revision_no: int
    approved_at: str
    approved_by: str  # actor of the APPROVE event bound to that revision ("" if none is recorded)
    content: QuestionContent
    content_hash: str


@dataclass(frozen=True, slots=True)
class ContractViolation:
    """A database state an exporter must not trust (the schema should make these impossible)."""

    uid: str
    problem: str


_SELECT = """
SELECT q.id, q.question_uid, q.subject_code, s.name_ka, s.sort_order, q.source_number,
       r.id, r.revision_no, q.approved_at,
       COALESCE((SELECT e.actor FROM review_events e
                 WHERE e.question_id = q.id AND e.action = 'APPROVE' AND e.revision_id = r.id
                 ORDER BY e.id DESC LIMIT 1), ''),
       r.stem, r.option_a, r.option_b, r.option_c, r.option_d, r.option_e, r.correct_letter,
       r.explanation, r.content_hash
FROM questions q
JOIN subjects s ON s.code = q.subject_code
JOIN question_revisions r ON r.id = q.approved_revision_id
WHERE q.status = 'APPROVED'
"""

_ORDER = " ORDER BY s.sort_order, q.source_number"


def _approved(row: sqlite3.Row | tuple) -> ApprovedQuestion:
    return ApprovedQuestion(
        question_id=int(row[0]),
        uid=row[1],
        subject_code=row[2],
        subject_name_ka=row[3],
        subject_order=int(row[4]),
        source_number=int(row[5]),
        revision_id=int(row[6]),
        revision_no=int(row[7]),
        approved_at=row[8],
        approved_by=row[9],
        content=QuestionContent(row[10], tuple(row[11:16]), AnswerLetter.from_letter(row[16]), row[17]),
        content_hash=row[18],
    )


def list_approved(conn: sqlite3.Connection, subject_code: str | None = None) -> list[ApprovedQuestion]:
    where, params = (" AND q.subject_code = ?", (subject_code,)) if subject_code else ("", ())
    return [_approved(r) for r in conn.execute(_SELECT + where + _ORDER, params)]


def get_approved(conn: sqlite3.Connection, uid: str) -> ApprovedQuestion | None:
    row = conn.execute(_SELECT + " AND q.question_uid = ?", (uid,)).fetchone()
    return _approved(row) if row else None


def approved_ids(conn: sqlite3.Connection, subject_code: str | None = None) -> list[int]:
    """Row ids of the approved questions in export order (subject order, then source number)."""
    where, params = (" AND q.subject_code = ?", (subject_code,)) if subject_code else ("", ())
    sql = (
        "SELECT q.id FROM questions q JOIN subjects s ON s.code = q.subject_code "
        "WHERE q.status = 'APPROVED' AND q.approved_revision_id IS NOT NULL" + where + _ORDER
    )
    return [int(r[0]) for r in conn.execute(sql, params)]


def count_by_subject(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT subject_code, count(*) FROM questions WHERE status = 'APPROVED' GROUP BY subject_code"
    )
    return {r[0]: int(r[1]) for r in rows}


def contract_violations(conn: sqlite3.Connection) -> list[ContractViolation]:
    """States in which an approved question is *not* safely exportable.

    The schema's CHECKs and triggers already make most of these impossible; this is the
    exporter's own belt-and-braces check, run before it writes a file.
    """
    found: list[ContractViolation] = []
    for uid, pointer, current in conn.execute(
        "SELECT question_uid, approved_revision_id, current_revision_id FROM questions "
        "WHERE status = 'APPROVED'"
    ):
        if pointer is None:
            found.append(ContractViolation(uid, "approved without an approved revision"))
        elif pointer != current:
            found.append(ContractViolation(uid, "approved revision is not the current revision"))
    for (uid,) in conn.execute(
        "SELECT question_uid FROM questions WHERE status <> 'APPROVED' "
        "AND (approved_revision_id IS NOT NULL OR approved_at IS NOT NULL)"
    ):
        found.append(ContractViolation(uid, f"carries approval pointers while not {ReviewStatus.APPROVED}"))
    for (uid,) in conn.execute(
        "SELECT q.question_uid FROM questions q WHERE q.status = 'APPROVED' AND NOT EXISTS ("
        " SELECT 1 FROM review_events e WHERE e.question_id = q.id AND e.action = 'APPROVE'"
        " AND e.revision_id = q.approved_revision_id)"
    ):
        found.append(ContractViolation(uid, "no APPROVE event is bound to the approved revision"))
    return found
