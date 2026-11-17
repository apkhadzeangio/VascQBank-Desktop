"""Seeding helpers for Phase 3 tests: synthetic questions written through the real repository.

Questions go in via :func:`vascqbank_desktop.db.question_repository.insert_import`, so every
database constraint and trigger applies exactly as in production. Texts are artificial.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import question_repository as repo
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.db.migrations import utc_now_iso
from vascqbank_desktop.domain.answers import ANSWER_LETTERS, AnswerLetter
from vascqbank_desktop.domain.question import (
    ImportedQuestion,
    QuestionContent,
    SourceKind,
    SourceLocation,
)
from vascqbank_desktop.domain.question_id import QuestionId
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.storage import initialize_storage


def make_question(
    code: str,
    number: int,
    *,
    stem: str | None = None,
    options: tuple[str, ...] | None = None,
    correct: AnswerLetter | None = None,
    explanation: str | None = None,
    kind: SourceKind = SourceKind.PAIRED_TXT,
) -> ImportedQuestion:
    correct = correct or AnswerLetter(ANSWER_LETTERS[(number * 7 + 3) % 5])
    return ImportedQuestion(
        qid=QuestionId(code, number),
        content=QuestionContent(
            stem=stem or f"სინთეზური სატესტო კითხვა {code} №{number}: რომელი ვარიანტია სწორი?",
            options=options
            or tuple(f"ვარიანტი {letter} — სატესტო ტექსტი {code}-{number}" for letter in ANSWER_LETTERS),
            correct=correct,
            explanation=explanation or f"ეს სინთეზური განმარტებაა {code} №{number}.",
        ),
        source=SourceLocation(
            kind=kind,
            file=f"{code}_questions.txt" if kind is SourceKind.PAIRED_TXT else "REFINED_MASTER_QBANK.txt",
            line=number * 7,
            explanation_file=f"{code}_explanations.txt" if kind is SourceKind.PAIRED_TXT else None,
            explanation_line=number * 3 if kind is SourceKind.PAIRED_TXT else None,
        ),
    )


def seed(paths: AppPaths, questions: Iterable[ImportedQuestion]) -> None:
    """Initialise storage (if needed) and insert *questions* as one import batch."""
    initialize_storage(paths)
    now = utc_now_iso()
    conn = open_database(paths.database_file)
    try:
        repo.insert_import(
            conn,
            list(questions),
            repo.BatchRecord(
                started_at=now,
                finished_at=now,
                source_summary="tests",
                skipped_identical_count=0,
                skipped_conflict_count=0,
                invalid_count=0,
                warning_count=0,
                backup_file=None,
                manifest={"format": "tests"},
            ),
            author="იმპორტი",
            now=now,
        )
    finally:
        conn.close()


def seed_counts(paths: AppPaths, counts: dict[str, int]) -> None:
    seed(paths, (make_question(code, n) for code, total in counts.items() for n in range(1, total + 1)))


def snapshot(paths: AppPaths) -> dict[str, list[tuple]]:
    """Every row of the authoritative tables, to prove an action changed nothing."""
    conn = sqlite3.connect(paths.database_file)
    try:
        return {
            table: [tuple(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
            for table in (
                "questions",
                "question_revisions",
                "review_events",
                "import_batches",
                "app_metadata",
            )
        }
    finally:
        conn.close()


def simulate_edit(
    paths: AppPaths, uid: str, *, stem: str | None = None, explanation: str | None = None
) -> None:
    """What the Phase 4 editor will do: append a revision and move the current pointer."""
    conn = open_database(paths.database_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        q = conn.execute(
            "SELECT q.id, q.status, q.lock_version, r.revision_no, r.stem, r.option_a, r.option_b, "
            "r.option_c, r.option_d, r.option_e, r.correct_letter, r.explanation "
            "FROM questions q JOIN question_revisions r ON r.id = q.current_revision_id "
            "WHERE q.question_uid = ?",
            (uid,),
        ).fetchone()
        now = utc_now_iso()
        cursor = conn.execute(
            "INSERT INTO question_revisions (question_id, revision_no, stem, option_a, option_b, option_c, "
            "option_d, option_e, correct_letter, explanation, origin, author, content_hash, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'EDIT', 'ტესტი', ?, ?)",
            (
                q["id"],
                q["revision_no"] + 1,
                stem if stem is not None else q["stem"],
                q["option_a"],
                q["option_b"],
                q["option_c"],
                q["option_d"],
                q["option_e"],
                q["correct_letter"],
                explanation if explanation is not None else q["explanation"],
                "0" * 64,
                now,
            ),
        )
        conn.execute(
            "UPDATE questions SET current_revision_id = ?, modified_at = ?, lock_version = lock_version + 1, "
            "status = CASE status WHEN 'PENDING_REVIEW' THEN 'IN_REVIEW' ELSE status END WHERE id = ?",
            (cursor.lastrowid, now, q["id"]),
        )
        conn.execute("COMMIT")
    finally:
        conn.close()


def set_status(paths: AppPaths, uid: str, status: ReviewStatus) -> None:
    """Move a question to *status* the way the future approval service will."""
    conn = open_database(paths.database_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = utc_now_iso()
        if status is ReviewStatus.APPROVED:
            conn.execute(
                "UPDATE questions SET status = 'APPROVED', approved_revision_id = current_revision_id, "
                "approved_at = ?, modified_at = ? WHERE question_uid = ?",
                (now, now, uid),
            )
        else:
            conn.execute(
                "UPDATE questions SET status = ?, approved_revision_id = NULL, approved_at = NULL, "
                "modified_at = ? WHERE question_uid = ?",
                (status.value, now, uid),
            )
        conn.execute("COMMIT")
    finally:
        conn.close()
