"""Migration 3 — unfinished editing drafts.

Drafts are *working copies*, kept strictly apart from the authoritative history:

* ``drafts`` holds at most one row per question: the text the reviewer is typing, the
  revision and ``lock_version`` it was started from, who typed it and when it was last
  written. Writing, replacing or deleting a draft never touches ``questions``,
  ``question_revisions`` or ``review_events`` — it cannot change a status, a timestamp,
  a lock version or any saved content.
* ``draft_discards`` is an append-only record of drafts a reviewer *chose* to throw away,
  so even an intentional (or mistaken) discard can still be recovered from the database.
* Draft text may be incomplete (an empty answer while typing), so unlike
  ``question_revisions`` its columns are not required to be non-empty.
* The base revision must belong to the same question, as with revision pointers.
"""

from __future__ import annotations

import sqlite3

from vascqbank_desktop.domain.answers import ANSWER_LETTERS

_LETTERS = ", ".join(f"'{x}'" for x in ANSWER_LETTERS)

_CONTENT_COLUMNS = f"""
        stem                TEXT    NOT NULL,
        option_a            TEXT    NOT NULL,
        option_b            TEXT    NOT NULL,
        option_c            TEXT    NOT NULL,
        option_d            TEXT    NOT NULL,
        option_e            TEXT    NOT NULL,
        correct_letter      TEXT    NOT NULL CHECK (correct_letter IN ({_LETTERS})),
        explanation         TEXT    NOT NULL,
        note                TEXT,
        reviewer_name       TEXT    NOT NULL CHECK (length(trim(reviewer_name)) > 0),
"""

STATEMENTS: list[str] = [
    f"""
    CREATE TABLE drafts (
        id                  INTEGER PRIMARY KEY,
        question_id         INTEGER NOT NULL UNIQUE REFERENCES questions(id),  -- one draft per question
        base_revision_id    INTEGER NOT NULL REFERENCES question_revisions(id),
        base_lock_version   INTEGER NOT NULL CHECK (base_lock_version >= 0),
        {_CONTENT_COLUMNS}
        write_count         INTEGER NOT NULL DEFAULT 1 CHECK (write_count >= 1),
        created_at          TEXT    NOT NULL,
        saved_at            TEXT    NOT NULL
    ) STRICT
    """,
    # Every foreign-key child column is indexed (see schema_v2 and tests/test_database.py).
    "CREATE INDEX drafts_base_revision ON drafts (base_revision_id)",
    """
    CREATE TRIGGER drafts_base_same_question_insert BEFORE INSERT ON drafts
    WHEN (SELECT question_id FROM question_revisions WHERE id = NEW.base_revision_id) IS NOT NEW.question_id
    BEGIN SELECT RAISE(ABORT, 'a draft must be based on a revision of the same question'); END
    """,
    """
    CREATE TRIGGER drafts_base_same_question_update BEFORE UPDATE OF question_id, base_revision_id ON drafts
    WHEN (SELECT question_id FROM question_revisions WHERE id = NEW.base_revision_id) IS NOT NEW.question_id
      OR NEW.question_id IS NOT OLD.question_id
    BEGIN SELECT RAISE(ABORT, 'a draft must be based on a revision of the same question'); END
    """,
    f"""
    CREATE TABLE draft_discards (
        id                  INTEGER PRIMARY KEY,
        question_id         INTEGER NOT NULL REFERENCES questions(id),
        base_revision_id    INTEGER NOT NULL REFERENCES question_revisions(id),
        base_lock_version   INTEGER NOT NULL,
        {_CONTENT_COLUMNS}
        draft_saved_at      TEXT    NOT NULL,
        discarded_at        TEXT    NOT NULL,
        discarded_by        TEXT    NOT NULL CHECK (length(trim(discarded_by)) > 0),
        reason              TEXT    NOT NULL CHECK (reason IN ('DISCARDED', 'REVERTED'))
    ) STRICT
    """,
    "CREATE INDEX draft_discards_question ON draft_discards (question_id, id)",
    "CREATE INDEX draft_discards_base_revision ON draft_discards (base_revision_id)",
    """CREATE TRIGGER draft_discards_no_update BEFORE UPDATE ON draft_discards
       BEGIN SELECT RAISE(ABORT, 'draft_discards is append-only'); END""",
    """CREATE TRIGGER draft_discards_no_delete BEFORE DELETE ON draft_discards
       BEGIN SELECT RAISE(ABORT, 'draft_discards is append-only'); END""",
]


def apply(conn: sqlite3.Connection) -> None:
    """Create the Phase 4 draft tables (called inside the migration transaction)."""
    for statement in STATEMENTS:
        conn.execute(statement)
