"""Phase 9 security / robustness: damaged or crafted journals and manifests must never make the
program delete, move or overwrite a file outside its own folders, and must never raise at start-up.

Every "victim" file below stands for a reviewer's unrelated file; each test proves it is untouched.
Synthetic data only.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from bank import seed_counts

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.services import export_publication, restore_service
from vascqbank_desktop.services.restore_service import RecoveryOutcome
from vascqbank_desktop.services.storage import initialize_storage

EXPORT_ID = "20260101-120000-abcdef"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _victim(folder: Path, name: str = "reviewer-notes.txt") -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    victim = folder / name
    victim.write_text("სინთეზური პირადი ფაილი — არ უნდა წაიშალოს", encoding="utf-8")
    return victim


def _export_runs(paths: AppPaths) -> int:
    conn = sqlite3.connect(paths.database_file)
    try:
        return int(conn.execute("SELECT count(*) FROM export_runs").fetchone()[0])
    finally:
        conn.close()


# ------------------------------------------------------------------------------- restore journal
def _restore_journal(paths: AppPaths, **overrides: object) -> dict[str, object]:
    live_sha = _sha(paths.database_file)
    data: dict[str, object] = {
        "format": 1,
        "stage": "prepared",
        "started_at": "2026-01-01T12:00:00+00:00",
        "app_version": "0.9.0",
        "backup": "vascqbank-20260101-110000-000000-manual.sqlite3",
        "backup_sha256": "1" * 64,
        "live_existed": True,
        "original_sha256": live_sha,
        "safety_backup": None,
        "temp": ".restore-" + "a" * 32 + ".partial",
        "temp_sha256": "2" * 64,
        "stamp": "20260101-120000-000000",
        "displaced": [],
    }
    data.update(overrides)
    return data


def _write_restore_journal(paths: AppPaths, data: object) -> None:
    path = restore_service.journal_path(paths)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def restore_bank(app_paths: AppPaths) -> AppPaths:
    seed_counts(app_paths, {"ALI": 3})
    return app_paths


def test_genuine_looking_journal_still_rolls_back(restore_bank: AppPaths) -> None:
    """Control: a journal with exactly our field shapes is trusted (Phase 8 behaviour kept)."""
    temp = restore_bank.database_dir / (".restore-" + "a" * 32 + ".partial")
    temp.write_bytes(b"half-written restore copy")
    _write_restore_journal(restore_bank, _restore_journal(restore_bank))
    result = restore_service.recover_interrupted_restore(restore_bank)
    assert result.outcome is RecoveryOutcome.ROLLED_BACK
    assert not temp.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"temp": "ABSOLUTE"},
        {"temp": "../../reviewer-notes.txt"},
        {"temp": "..\\..\\reviewer-notes.txt"},
        {"temp": ".restore-x.partial/../../reviewer-notes.txt"},
        {"stamp": "../../x"},
        {"displaced": ["../../reviewer-notes.txt"]},
        {"displaced": ["vascqbank-20260101-120000-000000-displaced.sqlite3", "reviewer-notes.txt"]},
        {"backup": "../reviewer-notes.txt"},
        {"safety_backup": "C:\\Users\\x\\notes.txt"},
        {"stage": "published-ish"},
        {"live_existed": "yes"},
        {"original_sha256": "not-a-hash"},
        {"format": 2},
    ],
)
def test_crafted_restore_journal_is_ambiguous_and_touches_nothing(
    restore_bank: AppPaths, tmp_path: Path, overrides: dict[str, object]
) -> None:
    victim = _victim(tmp_path / "home")
    if overrides.get("temp") == "ABSOLUTE":
        overrides = {"temp": str(victim)}
    live_before = restore_bank.database_file.read_bytes()
    _write_restore_journal(restore_bank, _restore_journal(restore_bank, **overrides))

    result = restore_service.recover_interrupted_restore(restore_bank)

    assert result.outcome is RecoveryOutcome.AMBIGUOUS
    assert victim.exists()
    assert restore_bank.database_file.read_bytes() == live_before
    assert restore_service.journal_path(restore_bank).exists()  # kept for the reviewer / support
    # Repeating start-up recovery is idempotent: still ambiguous, still nothing touched.
    assert restore_service.recover_interrupted_restore(restore_bank).outcome is RecoveryOutcome.AMBIGUOUS
    assert victim.exists()


@pytest.mark.parametrize(
    "content",
    [
        b"",
        b"\xff\xfe not utf-8 \xc3",
        b"[" * 100_000,
        b"{}",
        b"null",
        b'{"format": 1e999}',
        b'{"format": 1, "stage": "prepared", "temp": ' + b'"' + b"a" * 300 + b'"}',
    ],
)
def test_damaged_restore_journal_never_raises(restore_bank: AppPaths, content: bytes) -> None:
    path = restore_service.journal_path(restore_bank)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    result = restore_service.recover_interrupted_restore(restore_bank)
    assert result.outcome is RecoveryOutcome.AMBIGUOUS
    assert restore_bank.database_file.exists()


def test_oversized_restore_journal_is_not_parsed(restore_bank: AppPaths) -> None:
    path = restore_service.journal_path(restore_bank)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b" " * (restore_service.MAX_JOURNAL_BYTES + 1))
    assert restore_service._read_journal(restore_bank) is None


# -------------------------------------------------------------------------------- export journal
def _rows(export_id: str = EXPORT_ID) -> list[dict[str, object]]:
    return [
        {
            "format": "TXT",
            "scope": {"export_id": export_id},
            "question_count": 1,
            "files": ["questions.txt"],
            "app_version": "0.9.0",
            "created_at": "2026-01-01T12:00:00+00:00",
        }
    ]


def _manifest_folder(folder: Path, files: list[dict[str, object]]) -> str:
    folder.mkdir(parents=True)
    manifest = folder / export_publication.MANIFEST_NAME
    manifest.write_text(json.dumps({"files": files}), encoding="utf-8")
    return _sha(manifest)


def _write_export_journal(paths: AppPaths, entry: dict[str, object], name: str | None = None) -> Path:
    folder = export_publication.journal_dir(paths)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name or f"export-{EXPORT_ID}.json")
    path.write_text(json.dumps(entry), encoding="utf-8")
    return path


def _entry(dest: Path, digest: str, **overrides: object) -> dict[str, object]:
    entry: dict[str, object] = {
        "journal_format": 1,
        "export_id": EXPORT_ID,
        "final": str(dest / f"VascQBank_Export_{EXPORT_ID}"),
        "staging": str(dest / f".vqb-export-{EXPORT_ID}.partial"),
        "manifest_sha256": digest,
        "rows": _rows(),
    }
    entry.update(overrides)
    return entry


@pytest.fixture
def export_paths(app_paths: AppPaths) -> AppPaths:
    initialize_storage(app_paths)
    return app_paths


def test_staging_that_is_not_ours_is_never_deleted(export_paths: AppPaths, tmp_path: Path) -> None:
    dest = tmp_path / "exports"
    staging = dest / f".vqb-export-{EXPORT_ID}.partial"
    victim = _victim(staging)  # right name, but not the folder this journal staged
    journal = _write_export_journal(export_paths, _entry(dest, "3" * 64))

    report = export_publication.reconcile(export_paths)

    assert victim.exists()
    assert report.unverifiable == [EXPORT_ID] and not report.discarded
    assert not journal.exists()
    assert (journal.parent / "unverified" / journal.name).exists()
    assert _export_runs(export_paths) == 0
    # A second start-up does not touch it either (the journal was set aside).
    assert not export_publication.reconcile(export_paths).changed
    assert victim.exists()


@pytest.mark.parametrize(
    "overrides",
    [
        {"staging": "VICTIM"},
        {"final": "VICTIM"},
        {"staging": "relative/.vqb-export-20260101-120000-abcdef.partial"},
        {"export_id": "../../etc"},
        {"export_id": "20260101-120000-ABCDEF"},
        {"rows": []},
        {"rows": [{"format": "TXT"}]},
        {"rows": _rows("20990101-000000-000000")},
        {"manifest_sha256": "zz"},
        {"journal_format": 99},
    ],
)
def test_crafted_export_journal_is_set_aside_untouched(
    export_paths: AppPaths, tmp_path: Path, overrides: dict[str, object]
) -> None:
    dest = tmp_path / "exports"
    victim_dir = tmp_path / "home" / "Documents"
    victim = _victim(victim_dir)
    resolved = {k: (str(victim_dir) if v == "VICTIM" else v) for k, v in overrides.items()}
    journal = _write_export_journal(export_paths, _entry(dest, "3" * 64, **resolved))

    report = export_publication.reconcile(export_paths)

    assert victim.exists() and victim_dir.is_dir()
    assert report.unverifiable and not report.discarded and not report.recorded
    assert (journal.parent / "unreadable" / journal.name).exists()
    assert _export_runs(export_paths) == 0


def test_journal_file_name_must_match_export_id(export_paths: AppPaths, tmp_path: Path) -> None:
    dest = tmp_path / "exports"
    digest = _manifest_folder(dest / f".vqb-export-{EXPORT_ID}.partial", [])
    journal = _write_export_journal(
        export_paths, _entry(dest, digest), name="export-20260202-120000-abcdef.json"
    )
    report = export_publication.reconcile(export_paths)
    assert (dest / f".vqb-export-{EXPORT_ID}.partial").is_dir()  # not removed on a mismatched journal
    assert report.unverifiable and (journal.parent / "unreadable" / journal.name).exists()


@pytest.mark.parametrize(
    "bad_path",
    [
        "../reviewer-notes.txt",
        "/etc/passwd",
        "C:/Windows/win.ini",
        "a/../../reviewer-notes.txt",
        "",
        "x\x00y",
    ],
)
def test_published_manifest_paths_are_confined(export_paths: AppPaths, tmp_path: Path, bad_path: str) -> None:
    dest = tmp_path / "exports"
    victim = _victim(dest)
    final = dest / f"VascQBank_Export_{EXPORT_ID}"
    digest = _manifest_folder(final, [{"path": bad_path, "size": 1, "sha256": "0" * 64}])
    _write_export_journal(export_paths, _entry(dest, digest))

    report = export_publication.reconcile(export_paths)

    assert report.unverifiable == [EXPORT_ID] and not report.recorded
    assert victim.exists() and final.is_dir()  # a published folder is never deleted
    assert _export_runs(export_paths) == 0


def test_duplicate_manifest_entries_are_rejected(tmp_path: Path) -> None:
    folder = tmp_path / "VascQBank_Export_x"
    folder.mkdir()
    (folder / "a.txt").write_text("a", encoding="utf-8")
    entry = {"path": "a.txt", "size": 1, "sha256": _sha(folder / "a.txt")}
    manifest = folder / export_publication.MANIFEST_NAME
    manifest.write_text(json.dumps({"files": [entry, entry]}), encoding="utf-8")
    problems = export_publication.verify_published(folder, _sha(manifest))
    assert problems and "twice" in problems[0]


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_manifest_path_through_symlink_is_rejected(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    victim = _victim(outside)
    folder = tmp_path / "VascQBank_Export_x"
    folder.mkdir()
    (folder / "link").symlink_to(outside, target_is_directory=True)
    entry = {"path": "link/reviewer-notes.txt", "size": victim.stat().st_size, "sha256": _sha(victim)}
    manifest = folder / export_publication.MANIFEST_NAME
    manifest.write_text(json.dumps({"files": [entry]}), encoding="utf-8")
    problems = export_publication.verify_published(folder, _sha(manifest))
    assert problems and "symbolic link" in problems[0]


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_symlinked_journal_or_staging_is_not_followed(export_paths: AppPaths, tmp_path: Path) -> None:
    dest = tmp_path / "exports"
    dest.mkdir()
    victim_dir = tmp_path / "home"
    victim = _victim(victim_dir)
    (victim_dir / export_publication.MANIFEST_NAME).write_text("{}", encoding="utf-8")
    (dest / f".vqb-export-{EXPORT_ID}.partial").symlink_to(victim_dir, target_is_directory=True)
    digest = _sha(victim_dir / export_publication.MANIFEST_NAME)
    _write_export_journal(export_paths, _entry(dest, digest))
    report = export_publication.reconcile(export_paths)
    assert victim.exists() and report.unverifiable and not report.discarded


@pytest.mark.parametrize(
    "content",
    [b"\xff\xfe\x00garbage", b"[" * 100_000, b'{"journal_format": 1e999}', b"null", b"[]"],
)
def test_damaged_export_journal_never_raises(export_paths: AppPaths, content: bytes) -> None:
    folder = export_publication.journal_dir(export_paths)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"export-{EXPORT_ID}.json").write_bytes(content)
    report = export_publication.reconcile(export_paths)
    assert report.unverifiable
    assert (folder / "unreadable" / f"export-{EXPORT_ID}.json").exists()


def test_oversized_export_journal_is_not_parsed(export_paths: AppPaths, monkeypatch) -> None:
    monkeypatch.setattr(export_publication, "MAX_JOURNAL_BYTES", 64)
    folder = export_publication.journal_dir(export_paths)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"export-{EXPORT_ID}.json").write_text(json.dumps({"x": "y" * 100}), encoding="utf-8")
    assert export_publication.reconcile(export_paths).unverifiable


# ---------------------------------------------------------------------------------------- backups
def test_backup_manifest_without_hash_does_not_count_as_verified(app_paths: AppPaths) -> None:
    seed_counts(app_paths, {"ALI": 2})
    from vascqbank_desktop.services import backup_service

    path = backup_service.create_manual_backup(app_paths).path
    manifest = path.with_name(path.name + ".json")
    data = json.loads(manifest.read_text(encoding="utf-8"))
    del data["sha256"]
    manifest.write_text(json.dumps(data), encoding="utf-8")
    check = backup_db.check_backup_file(path)
    assert not check.ok and check.problem is backup_db.BackupProblem.MANIFEST_INVALID


def test_backup_manifest_is_written_before_the_backup_becomes_visible(
    app_paths: AppPaths, monkeypatch
) -> None:
    seed_counts(app_paths, {"ALI": 2})
    from vascqbank_desktop.db.connection import open_database

    def crash(*_args: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(backup_db, "_write_json_durably", crash)
    conn = open_database(app_paths.database_file)
    try:
        with pytest.raises(OSError):
            backup_db.create_backup(conn, app_paths.backups_dir, "manual")
        assert not conn.in_transaction
    finally:
        conn.close()
    assert list(app_paths.backups_dir.iterdir()) == []  # neither backup, partial nor manifest


def test_new_backup_manifest_is_complete(app_paths: AppPaths) -> None:
    seed_counts(app_paths, {"ALI": 2})
    from vascqbank_desktop.services import backup_service

    path = backup_service.create_manual_backup(app_paths).path
    manifest = backup_db.read_manifest(path)
    assert manifest is not None and not backup_db.manifest_problems(manifest)
    assert manifest["path"] == path.name and len(str(manifest["change_signature"])) == 64
    assert str(app_paths.data_dir) not in json.dumps(manifest, ensure_ascii=False)  # names only


def test_prune_matches_reason_exactly(tmp_path: Path) -> None:
    folder = tmp_path / "backups"
    folder.mkdir()
    names = [
        "vascqbank-20260101-000000-000000-pre-import.sqlite3",
        "vascqbank-20260101-000001-000000-pre-migration-v3.sqlite3",
        "vascqbank-20260101-000002-000000-manual-pre-import-copy.sqlite3",  # substring only
        "vascqbank-20260101-000003-000000-pre-importx.sqlite3",
        "vascqbank-20260101-000004-000000-manual.sqlite3",
        "vascqbank-20260101-000005-000000-pre-restore.sqlite3",
        "vascqbank-20260101-000006-000000-imported.sqlite3",
        "vascqbank-copy-pre-import.sqlite3",
    ]
    for name in names:
        (folder / name).write_bytes(b"x")
    removed = {p.name for p in backup_db.prune_automatic_backups(folder, 0)}
    assert removed == {names[0], names[1]}
    assert not backup_db.prune_automatic_backups(folder, 0, reasons=(backup_db.DAILY_REASON,))


@pytest.mark.parametrize(
    "content", [b"\xff\xfe", b"[" * 100_000, b'{"size": 1e999, "sha256": 5}', b"[]", b'"text"']
)
def test_damaged_backup_manifest_never_raises(app_paths: AppPaths, content: bytes) -> None:
    seed_counts(app_paths, {"ALI": 1})
    from vascqbank_desktop.services import backup_service

    path = backup_service.create_manual_backup(app_paths).path
    path.with_name(path.name + ".json").write_bytes(content)
    check = backup_db.check_backup_file(path)
    assert check.problem in (backup_db.BackupProblem.NONE, backup_db.BackupProblem.MANIFEST_INVALID)
    entries = backup_service.list_backup_entries(app_paths)
    assert len(entries) == 1
    with pytest.raises(restore_service.RestoreRefused):
        restore_service.inspect_backup(path)


@pytest.mark.skipif(sys.platform == "win32", reason="symbolic links need privileges on Windows")
def test_symlinked_backups_are_not_listed(app_paths: AppPaths, tmp_path: Path) -> None:
    app_paths.backups_dir.mkdir(parents=True)
    target = tmp_path / "elsewhere.sqlite3"
    target.write_bytes(b"x")
    (app_paths.backups_dir / "vascqbank-20260101-000000-000000-pre-import.sqlite3").symlink_to(target)
    assert backup_db.list_backups(app_paths.backups_dir) == []
    assert backup_db.prune_automatic_backups(app_paths.backups_dir, 0) == []
    assert target.exists()


# ------------------------------------------------------------------------------- single instance
def test_self_check_refuses_while_the_program_holds_the_lock(app_paths: AppPaths) -> None:
    from vascqbank_desktop.app.main import EXIT_ALREADY_RUNNING, acquire_instance_lock

    lock = acquire_instance_lock(app_paths)
    assert lock is not None
    try:
        env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "vascqbank_desktop",
                "--self-check",
                "--data-dir",
                str(app_paths.data_dir),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            env=env,
        )
    finally:
        lock.unlock()
    assert result.returncode == EXIT_ALREADY_RUNNING, result.stdout + result.stderr
    assert not app_paths.database_file.exists()  # nothing was created or recovered
