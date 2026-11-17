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


def approve(paths: AppPaths, uid: str, *, actor: str = "ტესტი") -> int:
    """Approve a question the way the Phase 5 service will: status, approval pointers and an
    APPROVE event bound to the current revision. Returns the approved revision's number."""
    conn = open_database(paths.database_file)
    try:
        conn.execute("BEGIN IMMEDIATE")
        now = utc_now_iso()
        row = conn.execute(
            "SELECT q.id, q.status, q.current_revision_id, r.revision_no FROM questions q "
            "JOIN question_revisions r ON r.id = q.current_revision_id WHERE q.question_uid = ?",
            (uid,),
        ).fetchone()
        conn.execute(
            "UPDATE questions SET status = 'APPROVED', approved_revision_id = current_revision_id, "
            "approved_at = ?, modified_at = ?, lock_version = lock_version + 1 WHERE id = ?",
            (now, now, row["id"]),
        )
        conn.execute(
            "INSERT INTO review_events (question_id, action, from_status, to_status, revision_id, actor, "
            "note, created_at) VALUES (?, 'APPROVE', ?, 'APPROVED', ?, ?, NULL, ?)",
            (row["id"], row["status"], row["current_revision_id"], actor, now),
        )
        conn.execute("COMMIT")
        return int(row["revision_no"])
    finally:
        conn.close()


def question_row(paths: AppPaths, uid: str) -> dict:
    """Authoritative state of one question plus its counts, for assertions."""
    conn = open_database(paths.database_file)
    try:
        q = dict(conn.execute("SELECT * FROM questions WHERE question_uid = ?", (uid,)).fetchone())
        q["revision_count"] = conn.execute(
            "SELECT count(*) FROM question_revisions WHERE question_id = ?", (q["id"],)
        ).fetchone()[0]
        q["event_count"] = conn.execute(
            "SELECT count(*) FROM review_events WHERE question_id = ?", (q["id"],)
        ).fetchone()[0]
        return q
    finally:
        conn.close()


def limit_database_size(monkeypatch, *modules) -> None:
    """Make every connection opened through these modules behave as if the disk were full:
    SQLite may not allocate a single page more than the file already has."""

    def full_disk(path):
        conn = open_database(path)
        pages = conn.execute("PRAGMA page_count").fetchone()[0]
        conn.execute(f"PRAGMA max_page_count = {pages}")
        return conn

    for module in modules:
        monkeypatch.setattr(module, "open_database", full_disk)


def draft_row(paths: AppPaths, uid: str) -> dict | None:
    """The draft row of a question (or ``None``), read straight from the database."""
    conn = open_database(paths.database_file)
    try:
        row = conn.execute(
            "SELECT d.* FROM drafts d JOIN questions q ON q.id = d.question_id WHERE q.question_uid = ?",
            (uid,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def revisions_of(paths: AppPaths, uid: str) -> list[dict]:
    """Every revision of a question, oldest first."""
    conn = open_database(paths.database_file)
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT r.* FROM question_revisions r JOIN questions q ON q.id = r.question_id "
                "WHERE q.question_uid = ? ORDER BY r.revision_no",
                (uid,),
            )
        ]
    finally:
        conn.close()
