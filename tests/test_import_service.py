"""Two-stage import: dry run, confirmed import, conflicts, rollback, integrity."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest
from synthetic import master_record, question_block, write_master, write_pair

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import question_repository as repo
from vascqbank_desktop.db.backup import list_backups, verify_backup
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.question import ImportedQuestion
from vascqbank_desktop.services import import_service as svc
from vascqbank_desktop.services.import_service import ImportAbortedError, Outcome
from vascqbank_desktop.services.question_stats import bank_counts
from vascqbank_desktop.services.storage import initialize_storage
from vascqbank_desktop.validation.issues import IssueCode


@pytest.fixture
def paths(app_paths: AppPaths) -> AppPaths:
    initialize_storage(app_paths)
    return app_paths


@pytest.fixture
def src(tmp_path: Path) -> Path:
    folder = tmp_path / "წყარო"
    write_pair(folder, "ALI", range(1, 6))
    write_pair(folder, "VENOUS", range(1, 4))
    return folder


def db(paths: AppPaths) -> sqlite3.Connection:
    return open_database(paths.database_file)


def scalar(paths: AppPaths, sql: str, *args):
    conn = db(paths)
    try:
        return conn.execute(sql, args).fetchone()[0]
    finally:
        conn.close()


def test_dry_run_writes_nothing(paths: AppPaths, src: Path) -> None:
    before = paths.database_file.read_bytes()
    report = svc.dry_run(paths, [src])
    counts = report.counts
    assert (counts.total_parsed, counts.valid, counts.eligible, counts.errors) == (8, 8, 8, 0)
    assert paths.database_file.read_bytes() == before
    assert list_backups(paths.backups_dir) == []


def test_confirmed_import_creates_pending_questions_with_history(paths: AppPaths, src: Path) -> None:
    report = svc.dry_run(paths, [src])
    result = svc.commit_import(paths, report)
    assert len(result.created) == 8
    assert verify_backup(result.backup.path)[0] >= 2

    conn = db(paths)
    try:
        rows = conn.execute(
            "SELECT q.question_uid, q.status, q.approved_revision_id, r.revision_no, r.origin, r.stem "
            "FROM questions q JOIN question_revisions r ON r.id = q.current_revision_id ORDER BY q.id"
        ).fetchall()
        assert [r["question_uid"] for r in rows][:2] == ["ALI-0001", "ALI-0002"]
        assert {r["status"] for r in rows} == {"PENDING_REVIEW"}
        assert {r["approved_revision_id"] for r in rows} == {None}
        assert {(r["revision_no"], r["origin"]) for r in rows} == {(1, "IMPORT")}
        # Georgian text preserved byte-for-byte
        assert rows[0]["stem"] == (
            "სინთეზური სატესტო კითხვა ALI №1: რომელი ვარიანტია სწორი ამ ხელოვნურ მაგალითში?"
        )
        original = json.loads(conn.execute(
            "SELECT original_content_json FROM questions WHERE question_uid='ALI-0001'").fetchone()[0])
        assert original["stem"] == rows[0]["stem"] and original["options"]["ა"].startswith("ვარიანტი ა")
        events = conn.execute("SELECT action, to_status FROM review_events").fetchall()
        assert len(events) == 8 and {tuple(e) for e in events} == {("IMPORTED", "PENDING_REVIEW")}
        batch = conn.execute("SELECT * FROM import_batches").fetchone()
        assert batch["created_count"] == 8 and batch["backup_file"] == result.backup.path.name
        manifest = json.loads(batch["manifest_json"])
        assert manifest["batch_id"] == result.batch_id
        assert len(manifest["created"]) == 8
        assert {f["name"] for s in manifest["sources"] for f in s["files"]} >= {"ALI_questions.txt"}
    finally:
        conn.close()
    assert bank_counts(paths).subjects["ALI"].total == 5


def test_repeated_import_finds_identical_and_imports_nothing(paths: AppPaths, src: Path) -> None:
    svc.commit_import(paths, svc.dry_run(paths, [src]))
    again = svc.dry_run(paths, [src])
    assert again.counts.identical_existing == 8 and again.counts.eligible == 0
    with pytest.raises(ImportAbortedError):
        svc.commit_import(paths, again)
    assert scalar(paths, "SELECT count(*) FROM questions") == 8
    assert scalar(paths, "SELECT count(*) FROM import_batches") == 1


def test_conflicts_never_overwrite_reviewer_edits(paths: AppPaths, src: Path) -> None:
    svc.commit_import(paths, svc.dry_run(paths, [src]))
    # simulate a reviewer edit: revision 2 becomes current
    conn = db(paths)
    conn.execute("BEGIN")
    qid, rev1 = conn.execute(
        "SELECT id, current_revision_id FROM questions WHERE question_uid='ALI-0002'").fetchone()
    r = conn.execute("SELECT * FROM question_revisions WHERE id=?", (rev1,)).fetchone()
    cur = conn.execute(
        "INSERT INTO question_revisions (question_id, revision_no, stem, option_a, option_b, option_c, "
        "option_d, option_e, correct_letter, explanation, origin, author, content_hash, created_at) "
        "VALUES (?, 2, 'რეცენზენტის ჩასწორებული ტექსტი', ?, ?, ?, ?, ?, ?, ?, 'EDIT', 'რეცენზენტი', ?, ?)",
        (qid, r["option_a"], r["option_b"], r["option_c"], r["option_d"], r["option_e"],
         r["correct_letter"], r["explanation"], "0" * 64, r["created_at"]))
    conn.execute("UPDATE questions SET current_revision_id=?, status='IN_REVIEW' WHERE id=?",
                 (cur.lastrowid, qid))
    conn.execute("COMMIT")
    conn.close()

    # upstream text changes for ALI-0002
    q = src / "ALI_questions.txt"
    q.write_text(q.read_text(encoding="utf-8").replace("ALI №2:", "ALI №2 (შეცვლილი):"), encoding="utf-8")
    report = svc.dry_run(paths, [src])
    conflict = next(c for c in report.candidates if c.uid == "ALI-0002")
    assert conflict.outcome is Outcome.CONFLICT_EXISTING and conflict.existing_has_edits
    assert IssueCode.CONFLICT_EXISTING in {i.code for i in conflict.issues}
    assert report.counts.eligible == 0 and report.counts.conflicts == 1

    stem = scalar(paths, "SELECT r.stem FROM questions q JOIN question_revisions r "
                         "ON r.id=q.current_revision_id WHERE q.question_uid='ALI-0002'")
    assert stem == "რეცენზენტის ჩასწორებული ტექსტი"


def test_only_new_questions_are_added_on_reimport(paths: AppPaths, src: Path) -> None:
    svc.commit_import(paths, svc.dry_run(paths, [src]))
    write_pair(src, "ALI", range(1, 8))  # two new questions appended upstream
    report = svc.dry_run(paths, [src])
    assert report.counts.identical_existing == 8 and report.counts.eligible == 2
    result = svc.commit_import(paths, report)
    assert result.created == ("ALI-0006", "ALI-0007")


def test_duplicate_ids_across_formats_are_not_imported(paths: AppPaths, src: Path, tmp_path: Path) -> None:
    master = write_master(tmp_path / "m", [master_record("ALI", 1), master_record("ALI", 900)])
    report = svc.dry_run(paths, [src, master])
    by_uid = {c.uid: c for c in report.candidates if c.uid}
    dup = [c for c in report.candidates if c.uid == "ALI-0001"]
    assert len(dup) == 2 and {c.outcome for c in dup} == {Outcome.DUPLICATE_IN_SELECTION}
    assert by_uid["ALI-0900"].outcome is Outcome.NEW
    assert report.counts.duplicates_in_selection == 2
    result = svc.commit_import(paths, report)
    assert "ALI-0001" not in result.created and "ALI-0900" in result.created


def test_duplicate_question_text_is_a_warning_only(paths: AppPaths, tmp_path: Path) -> None:
    folder = tmp_path / "dup"
    second = question_block("ALI", 2).replace("ALI №2", "ALI №1")
    questions = question_block("ALI", 1) + "\n\n" + second + "\n"
    write_pair(folder, "ALI", [1, 2], questions_text=questions)
    report = svc.dry_run(paths, [folder])
    assert report.counts.eligible == 2
    assert sum(1 for i in report.all_issues() if i.code is IssueCode.DUPLICATE_STEM) == 2


def test_invalid_questions_are_excluded(paths: AppPaths, tmp_path: Path) -> None:
    folder = tmp_path / "bad"
    questions = question_block("ALI", 1) + "\n\n" + question_block("ALI", 2).replace("*", "") + "\n"
    write_pair(folder, "ALI", [1, 2], questions_text=questions)
    report = svc.dry_run(paths, [folder])
    assert (report.counts.valid, report.counts.invalid, report.counts.eligible) == (1, 1, 1)
    assert svc.commit_import(paths, report).created == ("ALI-0001",)


def test_source_changed_after_dry_run_aborts_without_writing(paths: AppPaths, src: Path) -> None:
    report = svc.dry_run(paths, [src])
    (src / "ALI_questions.txt").write_text("1. შეცვლილი\n", encoding="utf-8")
    with pytest.raises(ImportAbortedError, match="changed"):
        svc.commit_import(paths, report)
    assert scalar(paths, "SELECT count(*) FROM questions") == 0
    assert list_backups(paths.backups_dir) == []


def test_failure_mid_import_rolls_back_everything(paths: AppPaths, src: Path, monkeypatch) -> None:
    report = svc.dry_run(paths, [src])
    original = ImportedQuestion.fingerprint

    def failing(self):
        # the 4th question violates CHECK(length(source_fingerprint) = 64)
        return "bad" if self.uid == "ALI-0004" else original(self)

    monkeypatch.setattr(ImportedQuestion, "fingerprint", failing)
    with pytest.raises(sqlite3.IntegrityError):
        svc.commit_import(paths, report)
    for table in ("questions", "question_revisions", "review_events", "import_batches"):
        assert scalar(paths, f"SELECT count(*) FROM {table}") == 0, table
    assert len(list_backups(paths.backups_dir)) == 1  # the verified pre-import backup


def test_question_appearing_after_dry_run_is_skipped_not_duplicated(paths: AppPaths, src: Path) -> None:
    report = svc.dry_run(paths, [src])
    first_only = svc.dry_run(paths, [src])
    first_only.candidates = [c for c in first_only.candidates if c.uid == "ALI-0001"]
    svc.commit_import(paths, first_only)
    result = svc.commit_import(paths, report)
    assert result.skipped_existing_at_commit == 1 and len(result.created) == 7
    assert scalar(paths, "SELECT count(*) FROM questions") == 8


# --- database-level integrity ------------------------------------------------
@pytest.fixture
def imported(paths: AppPaths, src: Path) -> AppPaths:
    svc.commit_import(paths, svc.dry_run(paths, [src]))
    return paths


@pytest.mark.parametrize(
    ("sql", "message"),
    [
        ("UPDATE questions SET original_content_json='{}' WHERE id=1", "immutable"),
        ("UPDATE questions SET question_uid='ALI-0999' WHERE id=1", "immutable"),
        ("DELETE FROM questions WHERE id=1", "never deleted"),
        ("UPDATE question_revisions SET stem='x' WHERE id=1", "append-only"),
        ("DELETE FROM review_events", "append-only"),
        ("DELETE FROM import_batches", "append-only"),
        ("UPDATE questions SET current_revision_id=2 WHERE id=1", "same question"),
        ("UPDATE questions SET status='APPROVED' WHERE id=1", "CHECK"),
    ],
)
def test_database_rejects_forbidden_changes(imported: AppPaths, sql: str, message: str) -> None:
    conn = db(imported)
    try:
        with pytest.raises(sqlite3.IntegrityError, match=message):
            conn.execute(sql)
    finally:
        conn.close()


def test_question_ids_are_unique_and_new_questions_cannot_be_preapproved(imported: AppPaths) -> None:
    conn = db(imported)
    try:
        conn.execute("BEGIN")
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE"):
            conn.execute(
                "INSERT INTO questions (question_uid, subject_code, source_number, import_batch_id, "
                "source_kind, source_file, source_line, source_fingerprint, original_content_json, "
                "current_revision_id, created_at, modified_at) "
                "VALUES ('ALI-0001', 'ALI', 1, 1, 'PAIRED_TXT', 'f', 1, ?, '{}', 1, 'now', 'now')",
                ("0" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="PENDING_REVIEW"):
            conn.execute(
                "INSERT INTO questions (question_uid, subject_code, source_number, status, "
                "import_batch_id, source_kind, source_file, source_line, source_fingerprint, "
                "original_content_json, current_revision_id, created_at, modified_at) "
                "VALUES ('ALI-0050', 'ALI', 50, 'APPROVED', 1, 'PAIRED_TXT', 'f', 1, ?, '{}', 1, 'n', 'n')",
                ("0" * 64,),
            )
        with pytest.raises(sqlite3.IntegrityError, match="CHECK"):
            conn.execute(
                "INSERT INTO questions (question_uid, subject_code, source_number, import_batch_id, "
                "source_kind, source_file, source_line, source_fingerprint, original_content_json, "
                "current_revision_id, created_at, modified_at) "
                "VALUES ('ALI-51', 'ALI', 51, 1, 'PAIRED_TXT', 'f', 1, ?, '{}', 1, 'n', 'n')",
                ("0" * 64,),
            )
        conn.execute("ROLLBACK")
    finally:
        conn.close()
    assert repo.load_existing(open_database(imported.database_file)).keys() >= {"ALI-0001"}
