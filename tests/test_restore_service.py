"""Restoring the working database from a verified backup (synthetic data in temporary folders only)."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest
from bank import approve, make_question, seed, seed_counts, simulate_edit

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.db.migrations import MIGRATIONS, migrate
from vascqbank_desktop.domain.question import QuestionContent
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services import restore_service
from vascqbank_desktop.services.backup_service import BackupKind, create_manual_backup, list_backup_entries
from vascqbank_desktop.services.draft_service import DraftService
from vascqbank_desktop.services.restore_service import (
    RecoveryOutcome,
    RestoreProblem,
    RestoreRefused,
    displaced_dir,
    inspect_backup,
    journal_path,
    recover_interrupted_restore,
    restore_backup,
)
from vascqbank_desktop.services.seed_service import sha256_file
from vascqbank_desktop.services.startup import EvidenceKind, Outcome, prepare_storage


# ----------------------------------------------------------------------------------- helpers
def questions(paths: AppPaths) -> list[tuple]:
    """(uid, status, current stem) of every question — the content a restore must bring back."""
    conn = sqlite3.connect(paths.database_file)
    try:
        return conn.execute(
            "SELECT q.question_uid, q.status, r.stem FROM questions q "
            "JOIN question_revisions r ON r.id = q.current_revision_id ORDER BY q.question_uid"
        ).fetchall()
    finally:
        conn.close()


def table_rows(path: Path, table: str) -> int:
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        return int(conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0])
    finally:
        conn.close()


def save_draft(paths: AppPaths, uid: str, stem: str) -> None:
    conn = open_database(paths.database_file)
    try:
        qid, rev, lock = conn.execute(
            "SELECT id, current_revision_id, lock_version FROM questions WHERE question_uid = ?", (uid,)
        ).fetchone()
        r = conn.execute(
            "SELECT option_a, option_b, option_c, option_d, option_e, correct_letter, explanation "
            "FROM question_revisions WHERE id = ?",
            (rev,),
        ).fetchone()
    finally:
        conn.close()
    from vascqbank_desktop.domain.answers import AnswerLetter

    content = QuestionContent(stem, tuple(r[:5]), AnswerLetter(r[5]), r[6])
    DraftService(paths).save_draft(qid, rev, lock, content, "რეცენზენტი")


@pytest.fixture
def restored_setup(app_paths: AppPaths):
    """A bank with an approval and a draft, a manual backup of it, then later work on top."""
    seed_counts(app_paths, {"ALI": 6, "VENOUS": 4})
    approve(app_paths, "ALI-0001")
    save_draft(app_paths, "ALI-0002", "მონახაზი, რომელიც ასლშია")
    backup = create_manual_backup(app_paths)
    in_backup = questions(app_paths)
    # later work, which a restore replaces (and which the safety backup must keep)
    approve(app_paths, "VENOUS-0003")
    simulate_edit(app_paths, "ALI-0004", stem="ასლის შემდეგ შეცვლილი ტექსტი")
    seed(app_paths, [make_question("ALI", 7)])
    later = questions(app_paths)
    assert later != in_backup
    return app_paths, backup.path, in_backup, later


# ----------------------------------------------------------------------------------- inspection
def test_inspect_describes_a_verified_backup(restored_setup) -> None:
    _paths, backup, in_backup, _later = restored_setup
    summary = inspect_backup(backup)
    assert summary.questions == len(in_backup) == 10
    assert summary.count(ReviewStatus.APPROVED) == 1 and summary.count(ReviewStatus.PENDING_REVIEW) == 9
    assert summary.drafts == 1 and summary.revisions == 10
    assert summary.schema_version == MIGRATIONS[-1].version
    assert summary.sha256 == sha256_file(backup) and summary.created_at and summary.reason == "manual"


# ----------------------------------------------------------------------------------- situation B
def test_restore_replaces_the_database_after_a_verified_safety_backup(restored_setup) -> None:
    paths, backup, in_backup, later = restored_setup
    paths.search_index_file.write_bytes(b"stale index")
    original_sha = sha256_file(paths.database_file)
    result = restore_backup(paths, backup, expected_sha256=inspect_backup(backup).sha256)

    assert questions(paths) == in_backup  # the backup's content, approvals included
    assert result.status.integrity_ok and result.status.database_id
    # the work done after the backup is in the verified safety backup ...
    assert result.safety_backup is not None and result.safety_backup.parent == paths.backups_dir
    assert backup_db.check_backup_file(result.safety_backup).ok
    assert table_rows(result.safety_backup, "questions") == len(later)
    entries = {e.name: e for e in list_backup_entries(paths)}
    assert entries[result.safety_backup.name].kind is BackupKind.BEFORE_RESTORE
    # ... and the replaced file itself is kept, byte for byte
    displaced = [p for p in result.displaced if p.name.endswith("-displaced.sqlite3")]
    assert len(displaced) == 1 and sha256_file(displaced[0]) == original_sha
    # derived data dropped, provenance recorded, no journal or temporary file left
    assert not paths.search_index_file.exists()
    meta = dict(sqlite3.connect(paths.database_file).execute("SELECT key, value FROM app_metadata"))
    assert meta["restored_from_backup"] == backup.name and meta["restored_from_sha256"] == sha256_file(backup)
    assert not journal_path(paths).exists() and not restore_service.stray_temporary_files(paths)
    # the restored database keeps its drafts and approvals and the backups are untouched
    assert table_rows(paths.database_file, "drafts") == 1
    assert backup_db.check_backup_file(backup).ok


def test_restoring_never_deletes_backups_and_safety_backups_are_never_pruned(restored_setup) -> None:
    paths, backup, *_ = restored_setup
    before = {p.name for p in backup_db.list_backups(paths.backups_dir)}
    result = restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    after = {p.name for p in backup_db.list_backups(paths.backups_dir)}
    assert before <= after and result.safety_backup and result.safety_backup.name in after
    assert result.safety_backup not in backup_db.prune_automatic_backups(paths.backups_dir, keep=0)


def test_repeated_restores_and_restoring_the_safety_backup_bring_the_later_work_back(restored_setup) -> None:
    paths, backup, in_backup, later = restored_setup
    first = restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    assert questions(paths) == in_backup
    assert first.safety_backup is not None
    restore_backup(paths, first.safety_backup, expected_sha256=sha256_file(first.safety_backup))
    assert questions(paths) == later
    restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    assert questions(paths) == in_backup


# ----------------------------------------------------------------------------------- situation A
def test_restore_into_a_missing_database_sets_stale_side_files_aside(app_paths: AppPaths) -> None:
    seed_counts(app_paths, {"ALI": 5})
    backup = create_manual_backup(app_paths).path
    expected = questions(app_paths)
    app_paths.database_file.unlink()
    stale_wal = Path(f"{app_paths.database_file}-wal")
    stale_wal.write_bytes(b"a stale write-ahead log of the lost database")
    outcome = prepare_storage(app_paths, None)
    assert outcome.outcome is Outcome.RECOVERY_NEEDED and not app_paths.database_file.exists()

    result = restore_backup(app_paths, backup, expected_sha256=sha256_file(backup))
    assert result.safety_backup is None
    assert questions(app_paths) == expected
    assert not stale_wal.exists()  # never replayed into the restored database ...
    kept = [p for p in result.displaced if p.name.endswith("-wal")]
    assert len(kept) == 1 and kept[0].read_bytes().startswith(b"a stale")  # ... but kept
    assert prepare_storage(app_paths, None).outcome is Outcome.READY


def test_a_database_that_appears_meanwhile_is_never_overwritten(app_paths: AppPaths, monkeypatch) -> None:
    seed_counts(app_paths, {"ALI": 3})
    backup = create_manual_backup(app_paths).path
    app_paths.database_file.unlink()

    def appear(stage: str) -> None:
        if stage == "displaced":
            app_paths.database_file.write_bytes(b"someone else's file")

    monkeypatch.setattr(restore_service, "_checkpoint", appear)
    with pytest.raises(RestoreRefused) as caught:
        restore_backup(app_paths, backup, expected_sha256=sha256_file(backup))
    assert caught.value.problem is RestoreProblem.PUBLISH_FAILED
    assert app_paths.database_file.read_bytes() == b"someone else's file"
    assert not journal_path(app_paths).exists() and not restore_service.stray_temporary_files(app_paths)


# ----------------------------------------------------------------------------------- refusals
def writable_copy(backup: Path, tmp: Path) -> Path:
    """A modified copy of a backup with a manifest that matches it again (so content checks must catch it)."""
    target = backup.parent / backup.name.replace("-manual", "-tampered")
    shutil.copy2(backup, target)
    os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    shutil.copy2(backup.with_name(backup.name + ".json"), target.with_name(target.name + ".json"))
    return target


def reseal(path: Path, *, counts: bool = False) -> None:
    manifest_path = path.with_name(path.name + ".json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["sha256"] = sha256_file(path)
    data["size"] = path.stat().st_size
    if counts:
        data.pop("table_counts", None)
    manifest_path.write_text(json.dumps(data), encoding="utf-8")


def refused(paths: AppPaths, backup: Path, problem: RestoreProblem, *, expected: str | None = None) -> None:
    original = sha256_file(paths.database_file)
    backups_before = {p.name for p in backup_db.list_backups(paths.backups_dir)}
    with pytest.raises(RestoreRefused) as caught:
        restore_backup(paths, backup, expected_sha256=expected or sha256_file(backup))
    assert caught.value.problem is problem, caught.value
    assert sha256_file(paths.database_file) == original  # the working database is untouched
    assert {p.name for p in backup_db.list_backups(paths.backups_dir)} == backups_before  # no safety backup
    assert not journal_path(paths).exists() and not restore_service.stray_temporary_files(paths)
    assert not displaced_dir(paths).exists() or not any(displaced_dir(paths).iterdir())


def test_a_backup_without_its_manifest_is_refused(restored_setup) -> None:
    paths, backup, *_ = restored_setup
    backup.with_name(backup.name + ".json").unlink()
    refused(paths, backup, RestoreProblem.NO_MANIFEST)


def test_a_sha256_mismatch_is_refused(restored_setup, tmp_path: Path) -> None:
    paths, backup, *_ = restored_setup
    copy = writable_copy(backup, tmp_path)
    data = bytearray(copy.read_bytes())
    data[-10] ^= 0xFF  # same size, different bytes
    copy.write_bytes(bytes(data))
    refused(paths, copy, RestoreProblem.CHECKSUM_MISMATCH)


def test_a_size_mismatch_is_refused(restored_setup, tmp_path: Path) -> None:
    paths, backup, *_ = restored_setup
    copy = writable_copy(backup, tmp_path)
    with copy.open("ab") as handle:
        handle.write(b"\0" * 4096)
    refused(paths, copy, RestoreProblem.SIZE_MISMATCH)


def test_a_foreign_key_violation_is_refused(restored_setup, tmp_path: Path) -> None:
    paths, backup, *_ = restored_setup
    copy = writable_copy(backup, tmp_path)
    conn = sqlite3.connect(copy)
    conn.execute("CREATE TABLE dangling (question_id INTEGER REFERENCES questions(id))")
    conn.execute("INSERT INTO dangling VALUES (99999)")
    conn.commit()
    conn.close()
    reseal(copy)
    refused(paths, copy, RestoreProblem.FOREIGN_KEYS)


def test_an_unsupported_schema_version_is_refused(restored_setup, tmp_path: Path) -> None:
    paths, backup, *_ = restored_setup
    copy = writable_copy(backup, tmp_path)
    conn = sqlite3.connect(copy)
    conn.execute("PRAGMA user_version = 99")
    conn.close()
    reseal(copy)
    refused(paths, copy, RestoreProblem.SCHEMA_UNSUPPORTED)


def test_another_programs_database_is_refused(restored_setup, tmp_path: Path) -> None:
    paths, backup, *_ = restored_setup
    copy = writable_copy(backup, tmp_path)
    conn = sqlite3.connect(copy)
    conn.execute("PRAGMA application_id = 12345")
    conn.close()
    reseal(copy)
    refused(paths, copy, RestoreProblem.FOREIGN_APPLICATION)


def test_a_damaged_database_is_refused(restored_setup, tmp_path: Path) -> None:
    paths, backup, *_ = restored_setup
    copy = writable_copy(backup, tmp_path)
    page_size = 4096
    data = bytearray(copy.read_bytes())
    for page in range(1, len(data) // page_size):  # every b-tree page header after the first page
        data[page * page_size : page * page_size + 12] = b"\xa5" * 12
    copy.write_bytes(bytes(data))
    reseal(copy)
    with pytest.raises(RestoreRefused) as caught:
        restore_backup(paths, copy, expected_sha256=sha256_file(copy))
    assert caught.value.problem in (RestoreProblem.INTEGRITY, RestoreProblem.UNREADABLE)


def test_row_counts_that_differ_from_the_manifest_are_refused(restored_setup, tmp_path: Path) -> None:
    paths, backup, *_ = restored_setup
    copy = writable_copy(backup, tmp_path)
    manifest_path = copy.with_name(copy.name + ".json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    data["table_counts"]["questions"] += 1
    manifest_path.write_text(json.dumps(data), encoding="utf-8")
    refused(paths, copy, RestoreProblem.COUNTS_MISMATCH)


def test_a_backup_changed_after_the_reviewer_checked_it_is_refused(restored_setup) -> None:
    paths, backup, *_ = restored_setup
    refused(paths, backup, RestoreProblem.CHANGED_SINCE_CHECK, expected="0" * 64)


def test_a_database_in_use_by_another_connection_is_not_replaced(restored_setup) -> None:
    paths, backup, *_ = restored_setup
    original = sha256_file(paths.database_file)
    other = open_database(paths.database_file)  # e.g. a second program window, merely open
    try:
        other.execute("SELECT count(*) FROM questions").fetchone()
        with pytest.raises(RestoreRefused) as caught:
            restore_backup(paths, backup, expected_sha256=sha256_file(backup))
        assert caught.value.problem is RestoreProblem.DATABASE_BUSY
    finally:
        other.close()
    assert sha256_file(paths.database_file) == original
    assert not journal_path(paths).exists() and not restore_service.stray_temporary_files(paths)


# ----------------------------------------------------------------------------------- injected errors
@pytest.mark.parametrize(
    "stage", ["verified", "safety-backup", "temp-created", "journal-written", "displaced", "replaced"]
)
def test_an_error_before_publication_leaves_the_previous_database(restored_setup, monkeypatch, stage) -> None:
    paths, backup, _in_backup, later = restored_setup

    def fail(name: str) -> None:
        if name == stage:
            raise OSError(f"injected failure at {name}")

    monkeypatch.setattr(restore_service, "_checkpoint", fail)
    with pytest.raises((RestoreRefused, OSError)):
        restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    if stage == "replaced":  # the swap itself already happened: the in-process recovery completes it
        assert questions(paths) != later
    else:
        assert questions(paths) == later
    assert not journal_path(paths).exists() and not restore_service.stray_temporary_files(paths)
    assert prepare_storage(paths, None).outcome is Outcome.READY


@pytest.mark.parametrize("stage", ["index-removed", "completed"])
def test_a_restored_database_that_cannot_be_reopened_is_set_aside(restored_setup, monkeypatch, stage) -> None:
    paths, backup, _in_backup, later = restored_setup

    def fail(name: str) -> None:
        if name == stage:
            raise RuntimeError("injected reopen failure")

    monkeypatch.setattr(restore_service, "_checkpoint", fail)
    with pytest.raises(RestoreRefused) as caught:
        restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    assert caught.value.problem is RestoreProblem.REOPEN_FAILED
    assert questions(paths) == later  # the previous database is back in place
    assert any(p.name.endswith("-restored-unusable.sqlite3") for p in displaced_dir(paths).iterdir())
    assert not journal_path(paths).exists()


# ----------------------------------------------------------------------------------- recovery
def test_an_ambiguous_interrupted_restore_touches_nothing(restored_setup, monkeypatch) -> None:
    paths, backup, *_ = restored_setup

    def crash(name: str) -> None:
        if name == "displaced":
            raise SystemExit("simulated crash")  # BaseException: no clean-up in a real crash either

    monkeypatch.setattr(restore_service, "_checkpoint", crash)
    def died(*_args):
        raise SystemExit("the process died")

    monkeypatch.setattr(restore_service, "_recover", died)  # no clean-up in a real crash
    with pytest.raises(SystemExit):
        restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    monkeypatch.undo()
    # a third file now sits where the database should be: neither the original nor the restored copy
    third = sqlite3.connect(paths.database_file)
    third.execute("CREATE TABLE IF NOT EXISTS something_else (x)")
    third.commit()
    third.close()
    before = sorted(p.name for p in paths.data_dir.rglob("*") if p.is_file())
    result = prepare_storage(paths, None)
    assert result.outcome is Outcome.RESTORE_INTERRUPTED
    assert result.restore_recovery and result.restore_recovery.outcome is RecoveryOutcome.AMBIGUOUS
    assert paths.database_file in result.restore_recovery.candidates
    assert sorted(p.name for p in paths.data_dir.rglob("*") if p.is_file()) == before  # nothing changed
    assert journal_path(paths).exists()


def test_an_unreadable_journal_is_ambiguous(restored_setup) -> None:
    paths, *_ = restored_setup
    journal_path(paths).parent.mkdir(parents=True, exist_ok=True)
    journal_path(paths).write_text("{not json", encoding="utf-8")
    assert recover_interrupted_restore(paths).outcome is RecoveryOutcome.AMBIGUOUS
    assert journal_path(paths).exists()


def test_a_pending_journal_blocks_a_new_restore(restored_setup) -> None:
    paths, backup, *_ = restored_setup
    journal_path(paths).parent.mkdir(parents=True, exist_ok=True)
    journal_path(paths).write_text("{}", encoding="utf-8")
    with pytest.raises(RestoreRefused) as caught:
        restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    assert caught.value.problem is RestoreProblem.INTERRUPTED_RESTORE_PENDING


def test_a_stray_temporary_copy_without_a_journal_is_removed_at_start_up(restored_setup) -> None:
    paths, *_ = restored_setup
    stray = paths.database_dir / ".restore-0123.partial"
    stray.write_bytes(b"half a copy")
    result = recover_interrupted_restore(paths)
    assert result.outcome is RecoveryOutcome.NONE and stray in result.removed and not stray.exists()


def test_the_journal_holds_no_question_text(restored_setup, monkeypatch) -> None:
    paths, backup, *_ = restored_setup
    captured: list[str] = []

    def look(name: str) -> None:
        if name == "journal-written":
            captured.append(journal_path(paths).read_text(encoding="utf-8"))

    monkeypatch.setattr(restore_service, "_checkpoint", look)
    restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    assert captured and "სინთეზური" not in captured[0] and "ვარიანტი" not in captured[0]


def test_an_older_schema_backup_is_restored_and_migrated_behind_its_own_backup(app_paths: AppPaths) -> None:
    app_paths.ensure_directories()
    conn = open_database(app_paths.database_file)
    migrate(conn, migrations=MIGRATIONS[:2])  # a Phase 2/3 (v2) database
    old = backup_db.create_backup(conn, app_paths.backups_dir, "manual").path
    conn.close()
    seed_counts(app_paths, {"ALI": 2})  # migrates the live database to the latest version
    result = restore_backup(app_paths, old, expected_sha256=sha256_file(old))
    assert result.summary.schema_version == 2
    assert result.status.schema_version == MIGRATIONS[-1].version
    assert result.status.pre_migration_backup is not None


def test_the_displaced_folder_counts_as_evidence_of_a_lost_database(restored_setup) -> None:
    paths, backup, *_ = restored_setup
    restore_backup(paths, backup, expected_sha256=sha256_file(backup))
    paths.database_file.unlink()
    result = prepare_storage(paths, None)
    assert result.outcome is Outcome.RECOVERY_NEEDED
    assert EvidenceKind.DISPLACED in {e.kind for e in result.evidence}
