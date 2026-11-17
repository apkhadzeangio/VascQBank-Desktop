from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_mod
from vascqbank_desktop.db.backup import (
    BackupError,
    create_backup,
    list_backups,
    prune_automatic_backups,
    verify_backup,
)
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.db.migrations import LATEST_VERSION, MIGRATIONS, migrate
from vascqbank_desktop.services.storage import initialize_storage


@pytest.fixture
def live(app_paths: AppPaths):
    initialize_storage(app_paths)
    conn = open_database(app_paths.database_file)
    yield conn
    conn.close()


def test_backup_is_verified_self_contained_and_described(live, app_paths: AppPaths) -> None:
    live.execute("INSERT INTO app_metadata (key, value) VALUES ('probe', 'ქართული ტექსტი')")
    info = create_backup(live, app_paths.backups_dir, "manual")
    assert info.path.is_file()
    assert info.schema_version == LATEST_VERSION
    assert info.table_counts["app_metadata"] == 4
    assert hashlib.sha256(info.path.read_bytes()).hexdigest() == info.sha256
    # rollback-journal mode: no -wal/-shm needed next to a backup
    raw = sqlite3.connect(info.path)
    assert raw.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    assert raw.execute("SELECT value FROM app_metadata WHERE key='probe'").fetchone()[0] == "ქართული ტექსტი"
    raw.close()
    manifest = json.loads(info.manifest_path.read_text(encoding="utf-8"))
    assert manifest["sha256"] == info.sha256 and manifest["reason"] == "manual"
    assert not list(app_paths.backups_dir.glob("*.partial"))


def test_verify_rejects_corrupted_backup(live, app_paths: AppPaths) -> None:
    info = create_backup(live, app_paths.backups_dir, "manual")
    data = bytearray(info.path.read_bytes())
    data[4096:8192] = b"\xff" * 4096  # destroy the second page
    info.path.write_bytes(bytes(data))
    with pytest.raises(BackupError):
        verify_backup(info.path)


def test_verify_rejects_foreign_database(tmp_path: Path) -> None:
    other = tmp_path / "other.sqlite3"
    raw = sqlite3.connect(other)
    raw.execute("CREATE TABLE x (a)")
    raw.commit()
    raw.close()
    with pytest.raises(BackupError):
        verify_backup(other)


def test_failed_verification_leaves_nothing(live, app_paths: AppPaths, monkeypatch) -> None:
    def broken(_path: Path):
        raise BackupError("simulated")

    monkeypatch.setattr(backup_mod, "verify_backup", broken)
    with pytest.raises(BackupError):
        create_backup(live, app_paths.backups_dir, "pre-import")
    assert list(app_paths.backups_dir.iterdir()) == []


def test_prune_keeps_recent_automatic_and_all_manual(live, app_paths: AppPaths) -> None:
    for _ in range(4):
        create_backup(live, app_paths.backups_dir, "pre-import")
    manual = create_backup(live, app_paths.backups_dir, "manual")
    removed = prune_automatic_backups(app_paths.backups_dir, keep=2)
    assert len(removed) == 2
    remaining = list_backups(app_paths.backups_dir)
    assert manual.path in remaining and len(remaining) == 3
    assert len(list(app_paths.backups_dir.glob("*.json"))) == 3


def test_migration_from_phase1_database_is_backed_up_first(app_paths: AppPaths) -> None:
    app_paths.ensure_directories()
    conn = open_database(app_paths.database_file)
    migrate(conn, migrations=MIGRATIONS[:1])  # a Phase 1 (v1) database
    conn.close()

    status = initialize_storage(app_paths)
    assert status.schema_version == LATEST_VERSION
    assert status.applied_migrations == tuple(range(2, LATEST_VERSION + 1))
    assert status.pre_migration_backup is not None
    version, _db_id, counts = verify_backup(status.pre_migration_backup)
    assert version == 1 and "questions" not in counts
    assert "pre-migration-v1" in status.pre_migration_backup.name

    again = initialize_storage(app_paths)
    assert again.pre_migration_backup is None  # nothing to migrate, no new backup
