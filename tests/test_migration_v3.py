"""Migration 3 (drafts): upgrading a populated Phase 3 database, and the new constraints."""

from __future__ import annotations

import sqlite3

import pytest
from bank import approve, make_question, snapshot

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import question_repository as repo
from vascqbank_desktop.db.backup import verify_backup
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.db.migrations import LATEST_VERSION, MIGRATIONS, migrate, utc_now_iso
from vascqbank_desktop.services.storage import initialize_storage


@pytest.fixture
def phase3_database(app_paths: AppPaths) -> AppPaths:
    """A database exactly as Phase 3 left it: schema v2, questions imported, one approved."""
    app_paths.ensure_directories()
    conn = open_database(app_paths.database_file)
    assert migrate(conn, migrations=MIGRATIONS[:2]).to_version == 2
    now = utc_now_iso()
    repo.insert_import(
        conn,
        [make_question("ALI", n) for n in range(1, 6)] + [make_question("VENOUS", 1)],
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
    conn.close()
    approve(app_paths, "ALI-0002")
    return app_paths


def test_a_phase3_database_is_backed_up_then_upgraded_without_touching_any_question(
    phase3_database: AppPaths,
) -> None:
    before = snapshot(phase3_database)
    assert phase3_database.database_file.is_file()

    status = initialize_storage(phase3_database)
    assert LATEST_VERSION >= 3 and status.schema_version == LATEST_VERSION
    assert status.applied_migrations == tuple(range(3, LATEST_VERSION + 1))
    assert status.pre_migration_backup is not None and "pre-migration-v2" in status.pre_migration_backup.name
    version, _db_id, counts = verify_backup(status.pre_migration_backup)
    assert version == 2 and counts["questions"] == 6 and "drafts" not in counts

    assert snapshot(phase3_database) == before  # every question, revision and event is identical
    conn = sqlite3.connect(phase3_database.database_file)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert conn.execute("SELECT count(*) FROM drafts").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM draft_discards").fetchone()[0] == 0
        assert (
            conn.execute("SELECT name FROM schema_migrations WHERE version = 3")
            .fetchone()[0]
            .startswith("editing drafts")
        )
        assert conn.execute("SELECT status FROM questions WHERE question_uid = 'ALI-0002'").fetchone()[0] == (
            "APPROVED"
        )
    finally:
        conn.close()
    assert initialize_storage(phase3_database).applied_migrations == ()  # and it is idempotent


@pytest.fixture
def v3(app_paths: AppPaths):
    initialize_storage(app_paths)
    from bank import seed

    seed(app_paths, [make_question("ALI", n) for n in range(1, 4)])
    conn = open_database(app_paths.database_file)
    ids = {
        row["question_uid"]: (row["id"], row["current_revision_id"])
        for row in conn.execute("SELECT id, question_uid, current_revision_id FROM questions")
    }
    yield conn, ids
    conn.close()


def insert_draft(conn, question_id, revision_id, **overrides) -> None:
    values = {
        "question_id": question_id,
        "base_revision_id": revision_id,
        "base_lock_version": 0,
        "stem": "ა",
        "option_a": "ა",
        "option_b": "ბ",
        "option_c": "გ",
        "option_d": "დ",
        "option_e": "ე",
        "correct_letter": "ა",
        "explanation": "განმარტება",
        "reviewer_name": "დავით ფარღალავა",
        "created_at": "2026-11-17T00:00:00+00:00",
        "saved_at": "2026-11-17T00:00:00+00:00",
    }
    values.update(overrides)
    names = ", ".join(values)
    marks = ", ".join("?" for _ in values)
    conn.execute(f"INSERT INTO drafts ({names}) VALUES ({marks})", tuple(values.values()))


def test_a_draft_may_hold_unfinished_text_but_never_an_invalid_letter_or_blank_reviewer(v3) -> None:
    conn, ids = v3
    question_id, revision_id = ids["ALI-0001"]
    insert_draft(conn, question_id, revision_id, option_c="", stem="")  # still being typed
    assert conn.execute("SELECT count(*) FROM drafts").fetchone()[0] == 1
    other_id, other_rev = ids["ALI-0002"]
    with pytest.raises(sqlite3.IntegrityError):
        insert_draft(conn, other_id, other_rev, correct_letter="ვ")
    with pytest.raises(sqlite3.IntegrityError):
        insert_draft(conn, other_id, other_rev, reviewer_name="   ")


def test_there_is_at_most_one_draft_per_question(v3) -> None:
    conn, ids = v3
    question_id, revision_id = ids["ALI-0001"]
    insert_draft(conn, question_id, revision_id)
    with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
        insert_draft(conn, question_id, revision_id)


def test_a_draft_must_be_based_on_a_revision_of_its_own_question(v3) -> None:
    conn, ids = v3
    question_id, _ = ids["ALI-0001"]
    _, foreign_revision = ids["ALI-0002"]
    with pytest.raises(sqlite3.IntegrityError, match="same question"):
        insert_draft(conn, question_id, foreign_revision)
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY|same question"):
        insert_draft(conn, 999_999, foreign_revision)
    with pytest.raises(sqlite3.IntegrityError, match="same question"):  # also when re-pointing later
        insert_draft(conn, question_id, ids["ALI-0001"][1])
        conn.execute(
            "UPDATE drafts SET base_revision_id = ? WHERE question_id = ?", (foreign_revision, question_id)
        )


def test_a_draft_never_touches_the_authoritative_tables(v3) -> None:
    conn, ids = v3
    question_id, revision_id = ids["ALI-0003"]
    before = {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
        for t in ("questions", "question_revisions", "review_events")
    }
    insert_draft(conn, question_id, revision_id, stem="ახალი მონახაზი")
    conn.execute("UPDATE drafts SET stem = 'კიდევ ახალი', write_count = write_count + 1")
    conn.execute("DELETE FROM drafts")
    after = {
        t: [tuple(r) for r in conn.execute(f"SELECT * FROM {t} ORDER BY 1")]
        for t in ("questions", "question_revisions", "review_events")
    }
    assert after == before


def test_discarded_drafts_are_append_only(v3) -> None:
    conn, ids = v3
    question_id, revision_id = ids["ALI-0001"]
    columns = (
        "question_id, base_revision_id, base_lock_version, stem, option_a, option_b, option_c, option_d, "
        "option_e, correct_letter, explanation, note, reviewer_name, draft_saved_at, discarded_at, "
        "discarded_by, reason"
    )
    row = (question_id, revision_id, 0, "ა", "ა", "ბ", "გ", "დ", "ე", "ა", "განმარტება", None,
           "დავით ფარღალავა", "t", "t", "დავით ფარღალავა", "DISCARDED")  # fmt: skip
    conn.execute(f"INSERT INTO draft_discards ({columns}) VALUES ({', '.join('?' * 17)})", row)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("UPDATE draft_discards SET stem = 'x'")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM draft_discards")
    bad = (*row[:-1], "FORGOTTEN")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"INSERT INTO draft_discards ({columns}) VALUES ({', '.join('?' * 17)})", bad)
    assert conn.execute("SELECT count(*) FROM draft_discards").fetchone()[0] == 1
