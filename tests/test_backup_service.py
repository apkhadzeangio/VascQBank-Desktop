"""Backup list, manual backup and re-verification for the UI."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from bank import make_question, seed, snapshot

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.backup import BackupProblem
from vascqbank_desktop.services import backup_service as svc
from vascqbank_desktop.services.backup_service import BackupKind, VerificationStatus


@pytest.fixture
def populated(app_paths: AppPaths) -> AppPaths:
    seed(app_paths, [make_question("ALI", n) for n in range(1, 21)])
    return app_paths


def test_empty_backup_folder_lists_nothing(app_paths: AppPaths) -> None:
    app_paths.ensure_directories()
    assert svc.list_backup_entries(app_paths) == []


def test_manual_backup_is_created_verified_and_listed(populated: AppPaths) -> None:
    before = snapshot(populated)
    entry = svc.create_manual_backup(populated)
    assert entry.path.is_file() and entry.path.parent == populated.backups_dir
    assert entry.kind is BackupKind.MANUAL
    assert entry.status is VerificationStatus.VERIFIED and entry.has_manifest
    assert entry.question_count == 20 and entry.size > 0 and entry.schema_version is not None
    assert entry.created_at is not None and entry.created_at.endswith("+00:00")
    assert snapshot(populated) == before  # backing up never changes the live database

    (listed,) = svc.list_backup_entries(populated)
    assert listed.path == entry.path and listed.kind is BackupKind.MANUAL
    assert listed.status is VerificationStatus.UNCHECKED  # listing is fast; verifying is explicit
    assert listed.created_at == entry.created_at


def test_backups_are_listed_newest_first_with_their_kind(populated: AppPaths) -> None:
    from vascqbank_desktop.db.backup import create_backup
    from vascqbank_desktop.db.connection import open_database

    conn = open_database(populated.database_file)
    try:
        for reason in ("pre-import", "pre-migration-v2", "manual"):
            create_backup(conn, populated.backups_dir, reason)
            time.sleep(0.01)
    finally:
        conn.close()
    entries = svc.list_backup_entries(populated)
    assert [e.kind for e in entries] == [
        BackupKind.MANUAL,
        BackupKind.BEFORE_MIGRATION,
        BackupKind.BEFORE_IMPORT,
    ]
    assert [e.created_at for e in entries] == sorted((e.created_at for e in entries), reverse=True)


def test_verify_reports_a_good_backup_as_verified(populated: AppPaths) -> None:
    svc.create_manual_backup(populated)
    (entry,) = svc.list_backup_entries(populated)
    checked = svc.verify_entry(entry)
    assert checked.status is VerificationStatus.VERIFIED and checked.problem is BackupProblem.NONE
    assert checked.question_count == 20 and checked.has_manifest


def test_verify_detects_a_modified_backup(populated: AppPaths) -> None:
    svc.create_manual_backup(populated)
    (entry,) = svc.list_backup_entries(populated)
    with entry.path.open("r+b") as handle:  # flip one byte in the middle of the file
        handle.seek(entry.size // 2)
        byte = handle.read(1)
        handle.seek(entry.size // 2)
        handle.write(bytes([byte[0] ^ 0xFF]))
    checked = svc.verify_entry(entry)
    assert checked.status is VerificationStatus.FAILED
    assert checked.problem is BackupProblem.CHECKSUM_MISMATCH


def test_verify_detects_truncation_and_garbage(populated: AppPaths) -> None:
    svc.create_manual_backup(populated)
    (entry,) = svc.list_backup_entries(populated)
    entry.path.write_bytes(entry.path.read_bytes()[:4096])
    assert svc.verify_entry(entry).problem is BackupProblem.SIZE_MISMATCH
    entry.path.with_name(
        entry.path.name + ".json"
    ).unlink()  # without a manifest only integrity can be checked
    garbage = svc.verify_entry(svc.list_backup_entries(populated)[0])
    assert garbage.status is VerificationStatus.FAILED and garbage.problem is BackupProblem.UNREADABLE
    assert garbage.has_manifest is False


def test_backup_without_a_manifest_is_verified_by_integrity_only(populated: AppPaths) -> None:
    created = svc.create_manual_backup(populated)
    created.path.with_name(created.path.name + ".json").unlink()
    (entry,) = svc.list_backup_entries(populated)
    assert entry.has_manifest is False and entry.created_at is not None  # time is read from the file name
    checked = svc.verify_entry(entry)
    assert checked.status is VerificationStatus.VERIFIED and checked.has_manifest is False


def test_verify_detects_a_manifest_that_disagrees_about_the_contents(populated: AppPaths) -> None:
    created = svc.create_manual_backup(populated)
    manifest_path = created.path.with_name(created.path.name + ".json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["table_counts"]["questions"] = 999
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    checked = svc.verify_entry(svc.list_backup_entries(populated)[0])
    assert checked.status is VerificationStatus.FAILED and checked.problem is BackupProblem.COUNTS_MISMATCH


def test_verify_all_reports_each_entry_as_it_completes(populated: AppPaths) -> None:
    for _ in range(3):
        svc.create_manual_backup(populated)
        time.sleep(0.01)
    entries = svc.list_backup_entries(populated)
    seen: list[tuple[int, VerificationStatus]] = []
    results = svc.verify_all(entries, progress=lambda i, e: seen.append((i, e.status)))
    assert [i for i, _ in seen] == [0, 1, 2]
    assert all(r.status is VerificationStatus.VERIFIED for r in results)


def test_a_missing_file_is_reported_not_raised(populated: AppPaths) -> None:
    created = svc.create_manual_backup(populated)
    (entry,) = svc.list_backup_entries(populated)
    created.path.unlink()
    assert svc.verify_entry(entry).problem is BackupProblem.MISSING_FILE


def test_manual_backups_survive_pruning_of_automatic_ones(populated: AppPaths) -> None:
    from vascqbank_desktop.db.backup import prune_automatic_backups

    manual = svc.create_manual_backup(populated)
    assert prune_automatic_backups(populated.backups_dir, keep=0) == []
    assert manual.path.is_file()


def test_partial_files_are_not_listed_as_backups(populated: AppPaths) -> None:
    populated.backups_dir.mkdir(parents=True, exist_ok=True)
    (populated.backups_dir / "vascqbank-20260101-000000-000000-manual.sqlite3.partial").write_bytes(b"half")
    assert svc.list_backup_entries(populated) == []


def test_the_live_database_is_untouched_by_listing_and_verifying(populated: AppPaths) -> None:
    svc.create_manual_backup(populated)
    raw = sqlite3.connect(populated.database_file)
    count_before = raw.execute("SELECT count(*) FROM questions").fetchone()[0]
    raw.close()
    before = snapshot(populated)
    svc.verify_all(svc.list_backup_entries(populated))
    assert snapshot(populated) == before and count_before == 20


def test_backup_folder_can_hold_unrelated_files(populated: AppPaths) -> None:
    populated.backups_dir.mkdir(parents=True, exist_ok=True)
    (populated.backups_dir / "notes.txt").write_text("hello", encoding="utf-8")
    Path(populated.backups_dir / "vascqbank-odd-name.sqlite3").write_bytes(b"x")
    entries = svc.list_backup_entries(populated)
    assert [e.name for e in entries] == ["vascqbank-odd-name.sqlite3"]
    assert entries[0].kind is BackupKind.OTHER and entries[0].created_at is None
