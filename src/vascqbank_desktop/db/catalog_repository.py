"""Read-only SQL for browsing, searching and opening questions.

Nothing in this module writes: it contains no INSERT/UPDATE/DELETE (a test
checks that) and callers open their connections with ``PRAGMA query_only``,
so merely looking at a question can never change a status, a timestamp or a
revision.

Design for 10,000+ questions:

* a listing first resolves only the ordered **ids** (an index-ordered scan,
  ~2 ms for the whole bank); the text of a question is read later, one page
  of ids at a time, in a single ``IN (…)`` query — no per-row queries and no
  revision history;
* the current revision is reached through ``questions.current_revision_id``
  (primary-key lookup), never through ``MAX(revision_no)``;
* every user-supplied value is a bound parameter; the only interpolated SQL
  fragments are constants chosen from fixed dictionaries.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from vascqbank_desktop.domain.answers import AnswerLetter
from vascqbank_desktop.domain.catalog import (
    QuestionDetail,
    QuestionLocation,
    QuestionSummary,
    RevisionDetail,
    RevisionInfo,
    SortOrder,
)
from vascqbank_desktop.domain.question import QuestionContent, SourceKind
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.search import (
    Preview,
    browse_preview,
    like_pattern,
    match_preview,
)

INDEX_SCHEMA = "idx"
ID_CHUNK = 500  # far below SQLite's bound-variable limit

_ORDER_BY: dict[SortOrder, str] = {
    SortOrder.NUMBER: "s.sort_order, q.source_number",
    SortOrder.STATUS: (
        "CASE q.status WHEN 'PENDING_REVIEW' THEN 0 WHEN 'IN_REVIEW' THEN 1 "
        "WHEN 'REJECTED' THEN 2 ELSE 3 END, s.sort_order, q.source_number"
    ),
    SortOrder.MODIFIED: "q.modified_at DESC, s.sort_order, q.source_number",
}

# Must stay equivalent to domain.search.document_text(): ID, stem, five options,
# explanation, newline-separated. A test compares both back ends.
_DOCUMENT_SQL = (
    "q.question_uid || char(10) || r.stem || char(10) || r.option_a || char(10) || r.option_b"
    " || char(10) || r.option_c || char(10) || r.option_d || char(10) || r.option_e"
    " || char(10) || r.explanation"
)

_PREVIEW_STEM_CHARS = 600


def _filters(
    subject_code: str | None, status: ReviewStatus | None, drafts_only: bool = False
) -> tuple[list[str], list[object]]:
    clauses: list[str] = []
    params: list[object] = []
    if subject_code is not None:
        clauses.append("q.subject_code = ?")
        params.append(subject_code)
    if status is not None:
        clauses.append("q.status = ?")
        params.append(status.value)
    if drafts_only:  # drafts.question_id is UNIQUE, so this is an index lookup per question
        clauses.append("EXISTS (SELECT 1 FROM drafts d WHERE d.question_id = q.id)")
    return clauses, params


def _ids(
    conn: sqlite3.Connection,
    base_from: str,
    clauses: list[str],
    params: list[object],
    sort: SortOrder,
) -> list[int]:
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT q.id FROM {base_from}{where} ORDER BY {_ORDER_BY[sort]}"
    return [int(row[0]) for row in conn.execute(sql, params)]


_BROWSE_FROM = "questions q JOIN subjects s ON s.code = q.subject_code"
_CURRENT_FROM = (
    "questions q JOIN subjects s ON s.code = q.subject_code "
    "JOIN question_revisions r ON r.id = q.current_revision_id"
)


# --------------------------------------------------------------------------
# listing ids
# --------------------------------------------------------------------------
def list_ids(
    conn: sqlite3.Connection,
    subject_code: str | None,
    status: ReviewStatus | None,
    sort: SortOrder = SortOrder.NUMBER,
    *,
    drafts_only: bool = False,
) -> list[int]:
    clauses, params = _filters(subject_code, status, drafts_only)
    return _ids(conn, _BROWSE_FROM, clauses, params, sort)


def count_matching(
    conn: sqlite3.Connection,
    subject_code: str | None,
    status: ReviewStatus | None,
    *,
    drafts_only: bool = False,
) -> int:
    clauses, params = _filters(subject_code, status, drafts_only)
    where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
    return int(conn.execute(f"SELECT count(*) FROM questions q{where}", params).fetchone()[0])


def search_ids_like(
    conn: sqlite3.Connection,
    terms: Sequence[str],
    subject_code: str | None,
    status: ReviewStatus | None,
    sort: SortOrder = SortOrder.NUMBER,
    *,
    drafts_only: bool = False,
) -> list[int]:
    """Substring search without any index: every term must occur in the document text."""
    clauses, params = _filters(subject_code, status, drafts_only)
    term_clauses = [f"({_DOCUMENT_SQL}) LIKE ? ESCAPE '\\'" for _ in terms]
    term_params: list[object] = [like_pattern(t) for t in terms]
    return _ids(conn, _CURRENT_FROM, [*term_clauses, *clauses], [*term_params, *params], sort)


def search_ids_fts(
    conn: sqlite3.Connection,
    match_expression: str,
    subject_code: str | None,
    status: ReviewStatus | None,
    sort: SortOrder = SortOrder.NUMBER,
    *,
    drafts_only: bool = False,
) -> list[int]:
    """Search through the attached derived index (see :mod:`vascqbank_desktop.db.search_index`)."""
    clauses, params = _filters(subject_code, status, drafts_only)
    clauses.append(f"q.id IN (SELECT rowid FROM {INDEX_SCHEMA}.docs WHERE docs MATCH ?)")
    params.append(match_expression)
    return _ids(conn, _BROWSE_FROM, clauses, params, sort)


# --------------------------------------------------------------------------
# row content, one page at a time
# --------------------------------------------------------------------------
def fetch_summaries(
    conn: sqlite3.Connection,
    ids: Sequence[int],
    terms: tuple[str, ...] = (),
) -> list[QuestionSummary]:
    """Summaries for *ids*, in the order given; unknown ids are skipped.

    Without *terms* only the start of the stem is read for the preview; with
    terms the whole current revision is needed to find the matching excerpt.
    """
    by_id: dict[int, QuestionSummary] = {}
    for start in range(0, len(ids), ID_CHUNK):
        chunk = list(ids[start : start + ID_CHUNK])
        marks = ",".join("?" * len(chunk))
        if terms:
            sql = (
                "SELECT q.id, q.question_uid, q.subject_code, q.source_number, q.status, r.stem,"
                " r.option_a, r.option_b, r.option_c, r.option_d, r.option_e, r.explanation"
                f" FROM questions q JOIN question_revisions r ON r.id = q.current_revision_id"
                f" WHERE q.id IN ({marks})"
            )
        else:
            sql = (
                "SELECT q.id, q.question_uid, q.subject_code, q.source_number, q.status,"
                f" substr(r.stem, 1, {_PREVIEW_STEM_CHARS})"
                " FROM questions q JOIN question_revisions r ON r.id = q.current_revision_id"
                f" WHERE q.id IN ({marks})"
            )
        for row in conn.execute(sql, chunk):
            preview: Preview
            if terms:
                preview = match_preview(row[1], row[5], tuple(row[6:11]), row[11], terms)
            else:
                preview = browse_preview(row[5])
            by_id[int(row[0])] = QuestionSummary(
                id=int(row[0]),
                uid=row[1],
                subject_code=row[2],
                source_number=int(row[3]),
                status=ReviewStatus(row[4]),
                preview=preview,
            )
    return [by_id[i] for i in ids if i in by_id]


_DETAIL_SQL = """
SELECT q.id, q.question_uid, q.subject_code, s.name_ka, q.source_number, q.status,
       r.stem, r.option_a, r.option_b, r.option_c, r.option_d, r.option_e,
       r.correct_letter, r.explanation, r.id, r.revision_no, q.lock_version,
       q.source_kind, q.source_file, q.source_line, q.import_batch_id,
       q.created_at, q.modified_at, q.approved_at
FROM questions q
JOIN subjects s ON s.code = q.subject_code
JOIN question_revisions r ON r.id = q.current_revision_id
WHERE {where}
"""


def _detail(row: sqlite3.Row | None) -> QuestionDetail | None:
    if row is None:
        return None
    return QuestionDetail(
        id=int(row[0]),
        uid=row[1],
        subject_code=row[2],
        subject_name_ka=row[3],
        source_number=int(row[4]),
        status=ReviewStatus(row[5]),
        content=QuestionContent(
            stem=row[6],
            options=tuple(row[7:12]),
            correct=AnswerLetter.from_letter(row[12]),
            explanation=row[13],
        ),
        revision_id=int(row[14]),
        revision_no=int(row[15]),
        lock_version=int(row[16]),
        source_kind=SourceKind(row[17]),
        source_file=row[18],
        source_line=int(row[19]),
        import_batch_id=int(row[20]),
        created_at=row[21],
        modified_at=row[22],
        approved_at=row[23],
    )


def get_detail(conn: sqlite3.Connection, question_id: int) -> QuestionDetail | None:
    row = conn.execute(_DETAIL_SQL.format(where="q.id = ?"), (question_id,)).fetchone()
    return _detail(row)


def get_detail_by_uid(conn: sqlite3.Connection, uid: str) -> QuestionDetail | None:
    row = conn.execute(_DETAIL_SQL.format(where="q.question_uid = ?"), (uid,)).fetchone()
    return _detail(row)


# --------------------------------------------------------------------------
# locating
# --------------------------------------------------------------------------
def _location(row: sqlite3.Row | None) -> QuestionLocation | None:
    if row is None:
        return None
    return QuestionLocation(
        id=int(row[0]),
        uid=row[1],
        subject_code=row[2],
        source_number=int(row[3]),
        status=ReviewStatus(row[4]),
    )


def find_location(conn: sqlite3.Connection, uid: str) -> QuestionLocation | None:
    row = conn.execute(
        "SELECT id, question_uid, subject_code, source_number, status FROM questions WHERE question_uid = ?",
        (uid,),
    ).fetchone()
    return _location(row)


def find_location_by_id(conn: sqlite3.Connection, question_id: int) -> QuestionLocation | None:
    row = conn.execute(
        "SELECT id, question_uid, subject_code, source_number, status FROM questions WHERE id = ?",
        (question_id,),
    ).fetchone()
    return _location(row)


def nearest_id(
    conn: sqlite3.Connection,
    anchor_uid: str,
    subject_code: str | None,
    status: ReviewStatus | None,
) -> int | None:
    """Closest question to *anchor_uid* within a filter, in catalog (NUMBER) order.

    The first question after the anchor if there is one, otherwise the last one
    before it. Used to keep a sensible place when the remembered question no
    longer matches the filter. ``None`` if the filter matches nothing.
    """
    anchor = conn.execute(
        "SELECT s.sort_order, q.source_number FROM questions q "
        "JOIN subjects s ON s.code = q.subject_code WHERE q.question_uid = ?",
        (anchor_uid,),
    ).fetchone()
    if anchor is None:
        return None
    clauses, params = _filters(subject_code, status)
    where = f" AND {' AND '.join(clauses)}" if clauses else ""
    key = "(s.sort_order > ? OR (s.sort_order = ? AND q.source_number > ?))"
    after = conn.execute(
        f"SELECT q.id FROM {_BROWSE_FROM} WHERE {key}{where} ORDER BY s.sort_order, q.source_number LIMIT 1",
        [anchor[0], anchor[0], anchor[1], *params],
    ).fetchone()
    if after is not None:
        return int(after[0])
    key = "(s.sort_order < ? OR (s.sort_order = ? AND q.source_number < ?))"
    before = conn.execute(
        f"SELECT q.id FROM {_BROWSE_FROM} WHERE {key}{where} "
        "ORDER BY s.sort_order DESC, q.source_number DESC LIMIT 1",
        [anchor[0], anchor[0], anchor[1], *params],
    ).fetchone()
    return int(before[0]) if before is not None else None


# --------------------------------------------------------------------------
# revision history (read-only; one question at a time)
# --------------------------------------------------------------------------
_REVISION_INFO_SQL = """
SELECT r.id, r.question_id, r.revision_no, r.origin, r.author, r.note, r.created_at,
       r.id = q.current_revision_id,
       EXISTS (SELECT 1 FROM review_events a WHERE a.revision_id = r.id AND a.action = 'APPROVE'),
       e.from_status, e.to_status, e.note, src.revision_no
FROM question_revisions r
JOIN questions q ON q.id = r.question_id
LEFT JOIN review_events e ON e.revision_id = r.id AND e.action IN ('EDITED', 'RESTORE')
LEFT JOIN question_revisions src ON src.id = r.restored_from_revision_id
"""


def _revision_info(row: sqlite3.Row) -> RevisionInfo:
    return RevisionInfo(
        id=int(row[0]),
        question_id=int(row[1]),
        revision_no=int(row[2]),
        origin=row[3],
        author=row[4],
        note=row[5],
        created_at=row[6],
        is_current=bool(row[7]),
        was_approved=bool(row[8]),
        from_status=ReviewStatus(row[9]) if row[9] else None,
        to_status=ReviewStatus(row[10]) if row[10] else None,
        event_note=row[11],
        restored_from_revision_no=int(row[12]) if row[12] is not None else None,
    )


def list_revisions(conn: sqlite3.Connection, question_id: int) -> list[RevisionInfo]:
    """Revision metadata of one question, newest first — no text is read."""
    rows = conn.execute(
        f"{_REVISION_INFO_SQL} WHERE r.question_id = ? ORDER BY r.revision_no DESC", (question_id,)
    ).fetchall()
    return [_revision_info(r) for r in rows]


def get_revision(conn: sqlite3.Connection, revision_id: int) -> RevisionDetail | None:
    """One revision with its full text."""
    info_row = conn.execute(f"{_REVISION_INFO_SQL} WHERE r.id = ?", (revision_id,)).fetchone()
    if info_row is None:
        return None
    text = conn.execute(
        "SELECT stem, option_a, option_b, option_c, option_d, option_e, correct_letter, explanation"
        " FROM question_revisions WHERE id = ?",
        (revision_id,),
    ).fetchone()
    content = QuestionContent(text[0], tuple(text[1:6]), AnswerLetter.from_letter(text[6]), text[7])
    return RevisionDetail(_revision_info(info_row), content)
