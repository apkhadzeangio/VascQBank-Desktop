"""Phase 9b — a backup, seed or external backup whose schema is not exactly ours is refused.

A consistent SQLite file can still carry objects this application never created: a trigger that
would run inside the application's own transactions after a restore, a view, an index, or a table
whose CHECK / FOREIGN KEY was removed. ``integrity_check`` and ``foreign_key_check`` accept such a
file. Every tampered file below is a *correct* database with a correct manifest (resealed hash), so
only the schema comparison can catch it. Synthetic data in temporary folders only.
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import stat
from pathlib import Path

import pytest
from bank import approve, seed_counts
from seedbank import reseal_database, small_seed, writable

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.db.migrations import LATEST_VERSION, MIGRATIONS, migrate
from vascqbank_desktop.db.schema_check import expected_schema, schema_differences, schema_objects
from vascqbank_desktop.services import restore_service
from vascqbank_desktop.services.backup_portability import (
    BackupCopyError,
    CopyProblem,
    import_external_backup,
    save_backup_copy,
)
from vascqbank_desktop.services.backup_service import create_manual_backup
from vascqbank_desktop.services.restore_service import (
    RestoreProblem,
    RestoreRefused,
    inspect_backup,
    restore_backup,
)
from vascqbank_desktop.services.seed_service import SeedInvalid, SeedProblem, sha256_file, verify_seed
from vascqbank_desktop.services.startup import Outcome, prepare_storage


def _replace_sql(conn: sqlite3.Connection, name: str, old: str, new: str) -> None:
    sql = conn.execute("SELECT sql FROM sqlite_schema WHERE name = ?", (name,)).fetchone()[0]
    assert old in sql, (name, old)
    version = conn.execute("PRAGMA schema_version").fetchone()[0]
    conn.execute("PRAGMA writable_schema = ON")
    conn.execute("UPDATE sqlite_schema SET sql = ? WHERE name = ?", (sql.replace(old, new, 1), name))
    conn.execute("PRAGMA writable_schema = OFF")
    conn.execute(f"PRAGMA schema_version = {version + 1}")


# Each tampering keeps the file consistent: integrity_check and foreign_key_check still pass.
TAMPERINGS = {
    # the dangerous one: after a restore, every approval would silently delete all drafts
    "extra-trigger": lambda c: c.execute(
        "CREATE TRIGGER evil AFTER INSERT ON review_events BEGIN DELETE FROM drafts; END"
    ),
    "extra-view": lambda c: c.execute("CREATE VIEW extra AS SELECT id FROM questions"),
    "extra-index": lambda c: c.execute("CREATE INDEX extra_idx ON questions(status)"),
    "extra-table": lambda c: c.execute("CREATE TABLE extra (x TEXT) STRICT"),
    "dropped-immutability-trigger": lambda c: c.execute("DROP TRIGGER questions_no_delete"),
    "changed-trigger-body": lambda c: (
        c.execute("DROP TRIGGER question_revisions_no_delete"),
        c.execute(
            "CREATE TRIGGER question_revisions_no_delete BEFORE DELETE ON question_revisions"
            " BEGIN SELECT 1; END"
        ),
    ),
    "removed-check": lambda c: _replace_sql(c, "questions", "CHECK (source_number > 0)", ""),
    "removed-foreign-key": lambda c: _replace_sql(c, "drafts", "REFERENCES questions(id)", ""),
}


def _tamper(path: Path, how: str) -> None:
    conn = sqlite3.connect(path, isolation_level=None)
    try:
        TAMPERINGS[how](conn)
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        conn.execute("PRAGMA journal_mode = DELETE")
    finally:
        conn.close()


def _reseal_backup(path: Path) -> None:
    """Make the manifest vouch for the tampered file again (size, SHA-256, row counts)."""
    manifest_path = path.with_name(path.name + ".json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        data["table_counts"] = backup_db._table_counts(conn)
    finally:
        conn.close()
    data["sha256"] = sha256_file(path)
    data["size"] = path.stat().st_size
    manifest_path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def bank(app_paths: AppPaths) -> AppPaths:
    seed_counts(app_paths, {"ALI": 6, "VENOUS": 3})
    approve(app_paths, "ALI-0001")
    return app_paths


def _tampered_backup(paths: AppPaths, tmp_path: Path, how: str) -> Path:
    original = create_manual_backup(paths).path
    folder = tmp_path / "external"
    folder.mkdir(exist_ok=True)
    copy = folder / original.name
    shutil.copy2(original, copy)
    shutil.copy2(original.with_name(original.name + ".json"), copy.with_name(copy.name + ".json"))
    os.chmod(copy, stat.S_IRUSR | stat.S_IWUSR)
    _tamper(copy, how)
    _reseal_backup(copy)
    return copy


# ----------------------------------------------------------------------------------- the reference
@pytest.mark.parametrize("version", range(0, LATEST_VERSION + 1))
def test_a_database_migrated_on_disk_matches_the_reference_schema(tmp_path: Path, version: int) -> None:
    """The comparison must never refuse a database this application wrote (WAL file, real path)."""
    conn = open_database(tmp_path / "db.sqlite3")
    try:
        if version:
            migrate(conn, MIGRATIONS[:version])
        assert schema_differences(conn, version) == []
        assert schema_objects(conn) == expected_schema(version)
    finally:
        conn.close()


def test_every_real_backup_kind_is_still_accepted(bank: AppPaths) -> None:
    backup = create_manual_backup(bank).path
    assert backup_db.check_backup_file(backup).ok
    assert inspect_backup(backup).questions == 9


def test_an_older_schema_is_compared_with_its_own_version(tmp_path: Path) -> None:
    conn = open_database(tmp_path / "v2.sqlite3")
    try:
        migrate(conn, MIGRATIONS[:2])
        assert schema_differences(conn, 2) == []
        assert schema_differences(conn, 3)  # v3 adds the draft tables
    finally:
        conn.close()


def test_an_unknown_version_is_reported_not_raised(tmp_path: Path) -> None:
    conn = sqlite3.connect(":memory:")
    try:
        assert schema_differences(conn, 99) == ["no migrations for schema version 99"]
    finally:
        conn.close()


# ----------------------------------------------------------------------------------- refusals
@pytest.mark.parametrize("how", sorted(TAMPERINGS))
def test_a_tampered_backup_is_refused_by_restore_and_nothing_changes(
    bank: AppPaths, tmp_path: Path, how: str
) -> None:
    copy = _tampered_backup(bank, tmp_path, how)
    before = sha256_file(bank.database_file)
    backups_before = sorted(p.name for p in backup_db.list_backups(bank.backups_dir))
    with pytest.raises(RestoreRefused) as caught:
        inspect_backup(copy)
    assert caught.value.problem is RestoreProblem.SCHEMA_TAMPERED
    with pytest.raises(RestoreRefused) as caught:
        restore_backup(bank, copy, expected_sha256=sha256_file(copy))
    assert caught.value.problem is RestoreProblem.SCHEMA_TAMPERED
    assert sha256_file(bank.database_file) == before
    assert sorted(p.name for p in backup_db.list_backups(bank.backups_dir)) == backups_before
    assert not restore_service.journal_path(bank).exists()
    assert not restore_service.stray_temporary_files(bank)


@pytest.mark.parametrize("how", sorted(TAMPERINGS))
def test_a_tampered_backup_is_not_listed_as_verified(bank: AppPaths, tmp_path: Path, how: str) -> None:
    copy = _tampered_backup(bank, tmp_path, how)
    check = backup_db.check_backup_file(copy)
    assert not check.ok and check.problem is backup_db.BackupProblem.SCHEMA_MISMATCH


@pytest.mark.parametrize("how", ["extra-trigger", "removed-check"])
def test_a_tampered_external_backup_is_not_added(bank: AppPaths, tmp_path: Path, how: str) -> None:
    copy = _tampered_backup(bank, tmp_path, how)
    before = sorted(p.name for p in bank.backups_dir.iterdir())
    with pytest.raises(BackupCopyError) as caught:
        import_external_backup(bank, copy)
    assert caught.value.problem is CopyProblem.SOURCE_INVALID
    assert sorted(p.name for p in bank.backups_dir.iterdir()) == before


def test_a_tampered_backup_is_not_copied_elsewhere(bank: AppPaths, tmp_path: Path) -> None:
    copy = _tampered_backup(bank, tmp_path, "extra-trigger")
    target = tmp_path / "usb"
    target.mkdir()
    with pytest.raises(BackupCopyError) as caught:
        save_backup_copy(copy, target)
    assert caught.value.problem is CopyProblem.SOURCE_INVALID
    assert list(target.iterdir()) == []


def test_the_dangerous_trigger_would_really_have_run(bank: AppPaths, tmp_path: Path) -> None:
    """Why this matters: the trigger fires inside the program's own transaction (shown on a copy)."""
    copy = _tampered_backup(bank, tmp_path, "extra-trigger")
    conn = sqlite3.connect(copy, isolation_level=None)
    try:
        qid, rev = conn.execute("SELECT id, current_revision_id FROM questions LIMIT 1").fetchone()
        conn.execute(
            "INSERT INTO drafts (question_id, base_revision_id, base_lock_version, stem, option_a, option_b,"
            " option_c, option_d, option_e, correct_letter, explanation, reviewer_name, created_at, saved_at)"
            " SELECT id, current_revision_id, lock_version, 's', 'a', 'b', 'c', 'd', 'e', 'ა',"
            " 'x', 'r', 't', 't' FROM questions WHERE id = ?",
            (qid,),
        )
        assert conn.execute("SELECT count(*) FROM drafts").fetchone()[0] == 1
        conn.execute(
            "INSERT INTO review_events (question_id, action, from_status, to_status, revision_id, actor,"
            " created_at) VALUES (?, 'START_REVIEW', 'PENDING_REVIEW', 'IN_REVIEW', ?, 'x', 't')",
            (qid, rev),
        )
        assert conn.execute("SELECT count(*) FROM drafts").fetchone()[0] == 0
    finally:
        conn.close()


# ----------------------------------------------------------------------------------- seeds
@pytest.mark.parametrize(
    "how", ["extra-trigger", "extra-view", "removed-foreign-key", "dropped-immutability-trigger"]
)
def test_a_tampered_seed_is_refused_and_no_database_is_created(
    tmp_path: Path, app_paths: AppPaths, how: str
) -> None:
    seed = small_seed(tmp_path)
    database = seed / "vascqbank-seed.sqlite3"
    writable(database)
    _tamper(database, how)
    reseal_database(seed)
    with pytest.raises(SeedInvalid) as caught:
        verify_seed(seed)
    assert caught.value.problem is SeedProblem.SCHEMA_MISMATCH
    result = prepare_storage(app_paths, seed)
    assert result.outcome is Outcome.SEED_INVALID
    assert not app_paths.database_file.exists()


def test_an_untampered_seed_still_installs(tmp_path: Path, app_paths: AppPaths) -> None:
    result = prepare_storage(app_paths, small_seed(tmp_path))
    assert result.outcome is Outcome.READY and result.seed_install is not None
    conn = sqlite3.connect(app_paths.database_file)
    try:
        assert schema_differences(conn, LATEST_VERSION) == []
    finally:
        conn.close()
