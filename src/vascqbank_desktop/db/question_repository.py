"""SQL access for questions and imports.

This module is the only place that inserts into ``questions``
(tests/test_architecture.py enforces it), and it only ever inserts:
existing questions are never updated by an import.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass

from vascqbank_desktop import __version__
from vascqbank_desktop.domain.question import ImportedQuestion
from vascqbank_desktop.domain.review_status import ReviewStatus


@dataclass(frozen=True, slots=True)
class ExistingQuestion:
    uid: str
    source_fingerprint: str
    status: ReviewStatus
    revision_count: int

    @property
    def has_reviewer_edits(self) -> bool:
        return self.revision_count > 1


@dataclass(frozen=True, slots=True)
class BatchRecord:
    started_at: str
    finished_at: str
    source_summary: str
    skipped_identical_count: int
    skipped_conflict_count: int
    invalid_count: int
    warning_count: int
    backup_file: str | None
    manifest: dict[str, object]


@dataclass(frozen=True, slots=True)
class InsertOutcome:
    batch_id: int
    created: tuple[str, ...]
    skipped_existing: tuple[str, ...]  # appeared in the database after the dry run


def load_existing(conn: sqlite3.Connection) -> dict[str, ExistingQuestion]:
    rows = conn.execute(
        """
        SELECT q.question_uid, q.source_fingerprint, q.status, r.revision_no
        FROM questions q JOIN question_revisions r ON r.id = q.current_revision_id
        """
    ).fetchall()
    return {
        row[0]: ExistingQuestion(row[0], row[1], ReviewStatus(row[2]), int(row[3])) for row in rows
    }


def current_stems(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (row[0], row[1])
        for row in conn.execute(
            "SELECT q.question_uid, r.stem FROM questions q "
            "JOIN question_revisions r ON r.id = q.current_revision_id"
        )
    ]


def count_by_subject_and_status(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    counts: dict[str, dict[str, int]] = {}
    for code, status, n in conn.execute(
        "SELECT subject_code, status, count(*) FROM questions GROUP BY subject_code, status"
    ):
        counts.setdefault(code, {})[status] = int(n)
    return counts


def _next_id(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COALESCE(max(id), 0) + 1 FROM {table}").fetchone()[0])


def insert_import(
    conn: sqlite3.Connection,
    questions: Sequence[ImportedQuestion],
    batch: BatchRecord,
    *,
    author: str,
    now: str,
) -> InsertOutcome:
    """Insert new questions and the batch record in ONE transaction.

    Each question gets revision 1 (origin ``IMPORT``) and an ``IMPORTED``
    review event. A question whose ID already exists is skipped
    (``ON CONFLICT DO NOTHING``) — never updated. Any failure rolls back the
    whole import.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        batch_id = _next_id(conn, "import_batches")
        revision_id = _next_id(conn, "question_revisions")
        created: list[str] = []
        skipped: list[str] = []
        for question in questions:
            content = question.content
            cursor = conn.execute(
                """
                INSERT INTO questions (
                    question_uid, subject_code, source_number, status, import_batch_id,
                    source_kind, source_file, source_line, source_fingerprint,
                    original_content_json, current_revision_id, created_at, modified_at
                ) VALUES (?, ?, ?, 'PENDING_REVIEW', ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT DO NOTHING
                """,
                (
                    question.uid,
                    question.qid.subject_code,
                    question.qid.source_number,
                    batch_id,
                    question.source.kind.value,
                    question.source.file,
                    question.source.line,
                    question.fingerprint(),
                    json.dumps(question.original_payload(), ensure_ascii=False),
                    revision_id,
                    now,
                    now,
                ),
            )
            if cursor.rowcount == 0:
                skipped.append(question.uid)
                continue
            question_id = cursor.lastrowid
            conn.execute(
                """
                INSERT INTO question_revisions (
                    id, question_id, revision_no, stem, option_a, option_b, option_c, option_d,
                    option_e, correct_letter, explanation, origin, author, content_hash, created_at
                ) VALUES (?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, 'IMPORT', ?, ?, ?)
                """,
                (
                    revision_id,
                    question_id,
                    content.stem,
                    *content.options,
                    content.correct.value,
                    content.explanation,
                    author,
                    content.content_hash(),
                    now,
                ),
            )
            conn.execute(
                """
                INSERT INTO review_events (question_id, action, from_status, to_status,
                                           revision_id, actor, note, created_at)
                VALUES (?, 'IMPORTED', NULL, 'PENDING_REVIEW', ?, ?, ?, ?)
                """,
                (question_id, revision_id, author, f"import batch {batch_id}", now),
            )
            created.append(question.uid)
            revision_id += 1

        manifest = dict(batch.manifest)
        manifest["batch_id"] = batch_id
        manifest["created"] = created
        manifest["skipped_existing_at_commit"] = skipped
        conn.execute(
            """
            INSERT INTO import_batches (
                id, started_at, finished_at, app_version, source_summary, created_count,
                skipped_identical_count, skipped_conflict_count, invalid_count, warning_count,
                backup_file, manifest_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                batch_id,
                batch.started_at,
                batch.finished_at,
                __version__,
                batch.source_summary,
                len(created),
                batch.skipped_identical_count,
                batch.skipped_conflict_count + len(skipped),
                batch.invalid_count,
                batch.warning_count,
                batch.backup_file,
                json.dumps(manifest, ensure_ascii=False),
            ),
        )
        # Deferred pointers are checked again explicitly before committing.
        broken = conn.execute(
            """
            SELECT count(*) FROM questions q
            LEFT JOIN question_revisions r ON r.id = q.current_revision_id
            WHERE q.import_batch_id = ? AND (r.id IS NULL OR r.question_id <> q.id)
            """,
            (batch_id,),
        ).fetchone()[0]
        if broken:
            raise sqlite3.IntegrityError(f"{broken} imported questions have broken revision pointers")
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    return InsertOutcome(batch_id=batch_id, created=tuple(created), skipped_existing=tuple(skipped))


def latest_batches(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, object]]:
    rows = conn.execute(
        """
        SELECT id, finished_at, source_summary, created_count, skipped_identical_count,
               skipped_conflict_count, invalid_count, warning_count, backup_file
        FROM import_batches ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]
