"""Migration 2 — subjects, questions, revisions, imports, review events, exports.

Integrity is enforced by the database itself, not only by Python code:

* the immutable question ID ``<SUBJECT>-<NNNN>`` is checked against the
  subject code and source number;
* identity, provenance and the original imported content of a question can
  never be updated, and questions can never be deleted;
* a newly inserted question must be ``PENDING_REVIEW`` with no approval —
  nothing can enter the database already approved;
* ``APPROVED`` status and an approval revision/timestamp always go together;
* revisions, review events, import batches and export runs are append-only,
  and revision numbers per question are contiguous (1, 2, 3, …);
* current/approved revision pointers must belong to the same question.
"""

from __future__ import annotations

import sqlite3

from vascqbank_desktop.domain.answers import ANSWER_LETTERS
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.domain.subjects import SUBJECTS

_STATUSES = ", ".join(f"'{s.value}'" for s in ReviewStatus)
_LETTERS = ", ".join(f"'{x}'" for x in ANSWER_LETTERS)
_SOURCE_KINDS = "'PAIRED_TXT', 'REFINED_MASTER'"


def _append_only(table: str) -> list[str]:
    return [
        f"""CREATE TRIGGER {table}_no_update BEFORE UPDATE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
        f"""CREATE TRIGGER {table}_no_delete BEFORE DELETE ON {table}
            BEGIN SELECT RAISE(ABORT, '{table} is append-only'); END""",
    ]


STATEMENTS: list[str] = [
    """
    CREATE TABLE subjects (
        code          TEXT    PRIMARY KEY CHECK (code GLOB '[A-Z]*' AND length(code) BETWEEN 2 AND 32),
        name_ka       TEXT    NOT NULL CHECK (length(trim(name_ka)) > 0),
        target_count  INTEGER NOT NULL CHECK (target_count > 0),
        sort_order    INTEGER NOT NULL UNIQUE
    ) STRICT
    """,
    """
    CREATE TABLE import_batches (
        id                       INTEGER PRIMARY KEY,
        started_at               TEXT    NOT NULL,
        finished_at              TEXT    NOT NULL,
        app_version              TEXT    NOT NULL,
        source_summary           TEXT    NOT NULL,
        created_count            INTEGER NOT NULL CHECK (created_count >= 0),
        skipped_identical_count  INTEGER NOT NULL CHECK (skipped_identical_count >= 0),
        skipped_conflict_count   INTEGER NOT NULL CHECK (skipped_conflict_count >= 0),
        invalid_count            INTEGER NOT NULL CHECK (invalid_count >= 0),
        warning_count            INTEGER NOT NULL CHECK (warning_count >= 0),
        backup_file              TEXT,
        manifest_json            TEXT    NOT NULL CHECK (json_valid(manifest_json))
    ) STRICT
    """,
    f"""
    CREATE TABLE questions (
        id                     INTEGER PRIMARY KEY,
        question_uid           TEXT    NOT NULL UNIQUE,
        subject_code           TEXT    NOT NULL REFERENCES subjects(code),
        source_number          INTEGER NOT NULL CHECK (source_number > 0),
        status                 TEXT    NOT NULL DEFAULT 'PENDING_REVIEW' CHECK (status IN ({_STATUSES})),
        -- deferred: the batch row is written last, with its final counts,
        -- because import_batches is append-only
        import_batch_id        INTEGER NOT NULL
                               REFERENCES import_batches(id) DEFERRABLE INITIALLY DEFERRED,
        source_kind            TEXT    NOT NULL CHECK (source_kind IN ({_SOURCE_KINDS})),
        source_file            TEXT    NOT NULL,
        source_line            INTEGER NOT NULL,
        source_fingerprint     TEXT    NOT NULL CHECK (length(source_fingerprint) = 64),
        original_content_json  TEXT    NOT NULL CHECK (json_valid(original_content_json)),
        current_revision_id    INTEGER NOT NULL
                               REFERENCES question_revisions(id) DEFERRABLE INITIALLY DEFERRED,
        approved_revision_id   INTEGER
                               REFERENCES question_revisions(id) DEFERRABLE INITIALLY DEFERRED,
        lock_version           INTEGER NOT NULL DEFAULT 0 CHECK (lock_version >= 0),
        created_at             TEXT    NOT NULL,
        modified_at            TEXT    NOT NULL,
        approved_at            TEXT,
        UNIQUE (subject_code, source_number),
        CHECK (question_uid = subject_code || '-' ||
               CASE WHEN source_number < 10000 THEN printf('%04d', source_number)
                    ELSE CAST(source_number AS TEXT) END),
        CHECK ((status = 'APPROVED') = (approved_revision_id IS NOT NULL AND approved_at IS NOT NULL))
    ) STRICT
    """,
    f"""
    CREATE TABLE question_revisions (
        id                         INTEGER PRIMARY KEY,
        question_id                INTEGER NOT NULL REFERENCES questions(id),
        revision_no                INTEGER NOT NULL CHECK (revision_no > 0),
        stem                       TEXT    NOT NULL CHECK (length(trim(stem)) > 0),
        option_a                   TEXT    NOT NULL CHECK (length(trim(option_a)) > 0),  -- ა
        option_b                   TEXT    NOT NULL CHECK (length(trim(option_b)) > 0),  -- ბ
        option_c                   TEXT    NOT NULL CHECK (length(trim(option_c)) > 0),  -- გ
        option_d                   TEXT    NOT NULL CHECK (length(trim(option_d)) > 0),  -- დ
        option_e                   TEXT    NOT NULL CHECK (length(trim(option_e)) > 0),  -- ე
        correct_letter             TEXT    NOT NULL CHECK (correct_letter IN ({_LETTERS})),
        explanation                TEXT    NOT NULL CHECK (length(trim(explanation)) > 0),
        origin                     TEXT    NOT NULL CHECK (origin IN ('IMPORT', 'EDIT', 'RESTORE')),
        restored_from_revision_id  INTEGER REFERENCES question_revisions(id),
        author                     TEXT    NOT NULL CHECK (length(trim(author)) > 0),
        note                       TEXT,
        content_hash               TEXT    NOT NULL CHECK (length(content_hash) = 64),
        created_at                 TEXT    NOT NULL,
        UNIQUE (question_id, revision_no),
        CHECK ((origin = 'RESTORE') = (restored_from_revision_id IS NOT NULL))
    ) STRICT
    """,
    f"""
    CREATE TABLE review_events (
        id           INTEGER PRIMARY KEY,
        question_id  INTEGER NOT NULL REFERENCES questions(id),
        action       TEXT    NOT NULL CHECK (action IN ('IMPORTED', 'EDITED', 'START_REVIEW', 'APPROVE',
                                                        'REJECT', 'REOPEN', 'RESTORE', 'COMMENT')),
        from_status  TEXT    CHECK (from_status IS NULL OR from_status IN ({_STATUSES})),
        to_status    TEXT    NOT NULL CHECK (to_status IN ({_STATUSES})),
        revision_id  INTEGER REFERENCES question_revisions(id),
        actor        TEXT    NOT NULL,
        note         TEXT,
        created_at   TEXT    NOT NULL
    ) STRICT
    """,
    """
    CREATE TABLE export_runs (
        id              INTEGER PRIMARY KEY,
        format          TEXT    NOT NULL CHECK (format IN ('DOCX', 'TXT')),
        scope_json      TEXT    NOT NULL CHECK (json_valid(scope_json)),
        question_count  INTEGER NOT NULL CHECK (question_count >= 0),
        files_json      TEXT    NOT NULL CHECK (json_valid(files_json)),
        manifest_json   TEXT    NOT NULL CHECK (json_valid(manifest_json)),
        app_version     TEXT    NOT NULL,
        created_at      TEXT    NOT NULL
    ) STRICT
    """,
    # --- indexes ----------------------------------------------------------
    "CREATE INDEX questions_subject_status ON questions (subject_code, status)",
    "CREATE INDEX questions_status ON questions (status)",
    "CREATE INDEX questions_import_batch ON questions (import_batch_id)",
    "CREATE INDEX question_revisions_question ON question_revisions (question_id, revision_no DESC)",
    "CREATE INDEX review_events_question ON review_events (question_id, id)",
    # Every foreign-key child column is indexed. Without these, each inserted
    # parent row makes SQLite scan the child table for (deferred) references,
    # which turns a 10,000-question import quadratic.
    "CREATE INDEX questions_current_revision ON questions (current_revision_id)",
    "CREATE INDEX questions_approved_revision ON questions (approved_revision_id)",
    "CREATE INDEX question_revisions_restored_from ON question_revisions (restored_from_revision_id)",
    "CREATE INDEX review_events_revision ON review_events (revision_id)",
    # --- question integrity triggers ---------------------------------------
    """
    CREATE TRIGGER questions_insert_pending_only BEFORE INSERT ON questions
    WHEN NEW.status <> 'PENDING_REVIEW' OR NEW.approved_revision_id IS NOT NULL
         OR NEW.approved_at IS NOT NULL OR NEW.lock_version <> 0
    BEGIN SELECT RAISE(ABORT, 'new questions must be PENDING_REVIEW without approval'); END
    """,
    """
    CREATE TRIGGER questions_identity_immutable BEFORE UPDATE ON questions
    WHEN NEW.question_uid IS NOT OLD.question_uid
      OR NEW.subject_code IS NOT OLD.subject_code
      OR NEW.source_number IS NOT OLD.source_number
      OR NEW.import_batch_id IS NOT OLD.import_batch_id
      OR NEW.source_kind IS NOT OLD.source_kind
      OR NEW.source_file IS NOT OLD.source_file
      OR NEW.source_line IS NOT OLD.source_line
      OR NEW.source_fingerprint IS NOT OLD.source_fingerprint
      OR NEW.original_content_json IS NOT OLD.original_content_json
      OR NEW.created_at IS NOT OLD.created_at
    BEGIN SELECT RAISE(ABORT, 'question identity and original content are immutable'); END
    """,
    """
    CREATE TRIGGER questions_no_delete BEFORE DELETE ON questions
    BEGIN SELECT RAISE(ABORT, 'questions are never deleted'); END
    """,
    """
    CREATE TRIGGER questions_revision_pointers BEFORE UPDATE OF current_revision_id, approved_revision_id
    ON questions
    WHEN (SELECT question_id FROM question_revisions WHERE id = NEW.current_revision_id) IS NOT NEW.id
      OR (NEW.approved_revision_id IS NOT NULL AND
          (SELECT question_id FROM question_revisions WHERE id = NEW.approved_revision_id) IS NOT NEW.id)
    BEGIN SELECT RAISE(ABORT, 'revision pointer must reference a revision of the same question'); END
    """,
    """
    CREATE TRIGGER question_revisions_contiguous BEFORE INSERT ON question_revisions
    WHEN NEW.revision_no <> COALESCE(
        (SELECT max(revision_no) FROM question_revisions WHERE question_id = NEW.question_id), 0) + 1
    BEGIN SELECT RAISE(ABORT, 'revision numbers must be contiguous'); END
    """,
    *_append_only("question_revisions"),
    *_append_only("review_events"),
    *_append_only("import_batches"),
    *_append_only("export_runs"),
]


def apply(conn: sqlite3.Connection) -> None:
    """Create the Phase 2 schema (called inside the migration transaction)."""
    for statement in STATEMENTS:
        conn.execute(statement)
    conn.executemany(
        "INSERT INTO subjects (code, name_ka, target_count, sort_order) VALUES (?, ?, ?, ?)",
        [(s.code, s.name_ka, s.target_count, i) for i, s in enumerate(SUBJECTS, start=1)],
    )
