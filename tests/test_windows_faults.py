"""Phase 9b, campaign A — Windows file-system behaviour, tested on every platform.

* **Sharing violation**: on Windows a file opened by another program (antivirus, indexer, a second
  copy of the program, a viewer) cannot be replaced, renamed or deleted. Each test holds a *real*
  open handle in a separate process while the operation runs. On Windows the operation must refuse or
  roll back with a Georgian message and leave everything as it was; on Linux/macOS (where open files
  can be replaced) it must succeed completely. Both outcomes are checked exactly — nothing is skipped.
  The deterministic variant (``PermissionError`` at every replace/rename/unlink on every platform)
  is part of ``test_fault_injection.py``.
* **Read-only attribute** on the database, a backup and ``settings.json``.
* **Paths longer than 260 characters**, with Georgian letters and spaces (every other test already
  runs in a folder named ``მონაცემები ტესტი``).

Synthetic data in temporary folders only.
"""

from __future__ import annotations

import os
import sqlite3
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from bank import approve, seed_counts

from vascqbank_desktop.config.paths import (
    MAX_SQLITE_PATH_BYTES,
    MAX_SQLITE_PATH_CHARS,
    AppPaths,
    data_dir_too_long,
)
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.db.errors import DataDirTooLong
from vascqbank_desktop.services.backup_service import create_manual_backup
from vascqbank_desktop.services.restore_service import (
    RestoreRefused,
    inspect_backup,
    journal_path,
    restore_backup,
    stray_temporary_files,
)
from vascqbank_desktop.services.seed_service import sha256_file
from vascqbank_desktop.services.settings_service import SettingsStore
from vascqbank_desktop.services.startup import Outcome, prepare_storage

WINDOWS = sys.platform == "win32"

HOLDER = textwrap.dedent(
    """
    import sys
    handle = open(sys.argv[1], "rb")  # Python on Windows opens without FILE_SHARE_DELETE
    print("HOLDING", flush=True)
    sys.stdin.readline()
    handle.close()
    """
)


class Holder:
    """Another process with *path* open for reading, as an antivirus scanner or a viewer would."""

    def __init__(self, path: Path) -> None:
        self.child = subprocess.Popen(
            [sys.executable, "-c", HOLDER, str(path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        assert self.child.stdout is not None and self.child.stdout.readline().strip() == "HOLDING"

    def release(self) -> None:
        if self.child.poll() is None:
            assert self.child.stdin is not None
            self.child.stdin.write("\n")
            self.child.stdin.flush()
        self.child.wait(timeout=30)
        for stream in (self.child.stdin, self.child.stdout):
            if stream is not None:
                stream.close()


def _questions(paths: AppPaths) -> list[tuple]:
    conn = sqlite3.connect(paths.database_file)
    try:
        return conn.execute("SELECT question_uid, status, lock_version FROM questions ORDER BY 1").fetchall()
    finally:
        conn.close()


@pytest.fixture
def worked(app_paths: AppPaths) -> tuple[AppPaths, Path, list[tuple]]:
    seed_counts(app_paths, {"ALI": 12, "VENOUS": 4})
    backup = create_manual_backup(app_paths).path
    in_backup = _questions(app_paths)
    approve(app_paths, "ALI-0001")
    approve(app_paths, "ALI-0002")
    return app_paths, backup, in_backup


# ----------------------------------------------------------------------------------- sharing violations
def test_restore_while_another_program_holds_the_database_open(worked) -> None:
    paths, backup, in_backup = worked
    later = _questions(paths)
    holder = Holder(paths.database_file)
    try:
        try:
            restore_backup(paths, backup, expected_sha256=inspect_backup(backup).sha256)
        except RestoreRefused as refused:
            assert WINDOWS, f"refused on a platform that allows replacing open files: {refused}"
            assert _questions(paths) == later  # rolled back: the live database is as before
            from vascqbank_desktop.ui import strings

            assert strings.RS_PROBLEMS[refused.problem.value]  # a Georgian reason exists
        else:
            assert _questions(paths) == in_backup  # replaced completely (the holder keeps the old inode)
    finally:
        holder.release()
    assert not journal_path(paths).exists() and not stray_temporary_files(paths)
    result = prepare_storage(paths, None)  # the next start is ordinary
    assert result.outcome is Outcome.READY and result.status is not None and result.status.integrity_ok
    if WINDOWS:  # once the other program let go, the same restore works
        restore_backup(paths, backup, expected_sha256=inspect_backup(backup).sha256)
        assert _questions(paths) == in_backup


def test_settings_saved_while_another_program_holds_the_file(app_paths: AppPaths) -> None:
    store = SettingsStore(app_paths.settings_file)
    app_paths.data_dir.mkdir(parents=True, exist_ok=True)
    store.set_reviewer_name("პირველი სახელი")
    holder = Holder(app_paths.settings_file)
    try:
        try:
            store.set_reviewer_name("მეორე სახელი")
        except OSError:
            assert WINDOWS
            assert store.load().reviewer_name == "პირველი სახელი"  # the old file is intact
        else:
            assert store.load().reviewer_name == "მეორე სახელი"
    finally:
        holder.release()
    assert not list(app_paths.data_dir.glob("*.tmp"))
    store.set_reviewer_name("მესამე სახელი")
    assert store.load().reviewer_name == "მესამე სახელი"


def test_pruning_a_backup_that_another_program_holds(app_paths: AppPaths) -> None:
    seed_counts(app_paths, {"ALI": 3})
    from vascqbank_desktop.db.connection import open_database

    conn = open_database(app_paths.database_file)
    try:
        made = [backup_db.create_backup(conn, app_paths.backups_dir, "pre-import").path for _ in range(3)]
    finally:
        conn.close()
    holder = Holder(made[0])
    try:
        try:
            removed = backup_db.prune_automatic_backups(app_paths.backups_dir, keep=2)
        except OSError:
            assert WINDOWS
            removed = []
        assert removed in ([], [made[0]])
    finally:
        holder.release()
    for path in backup_db.list_backups(app_paths.backups_dir):  # whatever remains is complete
        assert backup_db.check_backup_file(path).ok


def test_a_backup_while_another_program_reads_the_database(worked) -> None:
    paths, _backup, _rows = worked
    holder = Holder(paths.database_file)
    try:
        entry = create_manual_backup(paths)  # only reads the live file: must work everywhere
    finally:
        holder.release()
    assert backup_db.check_backup_file(entry.path).ok


# ----------------------------------------------------------------------------------- read-only attribute
def _read_only(path: Path) -> None:
    os.chmod(path, stat.S_IREAD)


def _writable(path: Path) -> None:
    os.chmod(path, stat.S_IREAD | stat.S_IWRITE)


def test_a_read_only_backup_still_verifies_and_restores(worked) -> None:
    paths, backup, in_backup = worked
    _read_only(backup)
    _read_only(backup.with_name(backup.name + ".json"))
    try:
        assert backup_db.check_backup_file(backup).ok
        restore_backup(paths, backup, expected_sha256=inspect_backup(backup).sha256)
        assert _questions(paths) == in_backup
        assert sha256_file(backup) == inspect_backup(backup).sha256  # never modified
    finally:
        _writable(backup)
        _writable(backup.with_name(backup.name + ".json"))


def test_a_read_only_working_database_stops_cleanly_and_unchanged(worked) -> None:
    paths, _backup, _in_backup = worked
    rows = _questions(paths)
    before = sha256_file(paths.database_file)
    _read_only(paths.database_file)
    try:
        try:
            result = prepare_storage(paths, None)
        except (sqlite3.Error, OSError):
            pass  # the program shows its Georgian start-up error; nothing was changed
        else:  # root on POSIX ignores the attribute: then it simply opens
            assert result.outcome is Outcome.READY
    finally:
        _writable(paths.database_file)
    assert _questions(paths) == rows
    if sha256_file(paths.database_file) != before:  # only an ordinary WAL checkpoint may differ
        conn = sqlite3.connect(paths.database_file)
        try:
            assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        finally:
            conn.close()


def test_a_read_only_settings_file_never_loses_the_previous_values(app_paths: AppPaths) -> None:
    store = SettingsStore(app_paths.settings_file)
    app_paths.data_dir.mkdir(parents=True, exist_ok=True)
    store.set_reviewer_name("პირველი სახელი")
    _read_only(app_paths.settings_file)
    try:
        try:
            store.set_reviewer_name("მეორე სახელი")
        except OSError:
            assert store.load().reviewer_name == "პირველი სახელი"
    finally:
        _writable(app_paths.settings_file)
    assert store.load().reviewer_name in ("პირველი სახელი", "მეორე სახელი")
    assert not list(app_paths.data_dir.glob("*.tmp"))


# ----------------------------------------------------------------------------------- long paths
def _units(path: Path) -> int:
    text = str(path.resolve())
    return len(text.encode("utf-16-le")) // 2 if WINDOWS else len(os.fsencode(text))


def _folder_with_longest_path(root: Path, target: int) -> AppPaths:
    """A data folder (Georgian, spaces) whose longest internal SQLite path is exactly *target* units."""
    folder = root / "მონაცემები და სივრცე"
    while _units(AppPaths(data_dir=folder / "ძალიან გრძელი საქაღალდე").longest_internal_path()) < target:
        folder = folder / "ძალიან გრძელი საქაღალდე"
    while _units(AppPaths(data_dir=folder).longest_internal_path()) < target:
        folder = folder.with_name(folder.name + "x")
    paths = AppPaths(data_dir=folder)
    assert _units(paths.longest_internal_path()) == target
    return paths


LIMIT = MAX_SQLITE_PATH_CHARS if WINDOWS else MAX_SQLITE_PATH_BYTES


def test_a_data_folder_at_the_path_limit_supports_every_operation(tmp_path: Path) -> None:
    paths = _folder_with_longest_path(tmp_path, LIMIT)
    assert not data_dir_too_long(paths)
    assert prepare_storage(paths, None).outcome is Outcome.READY
    from synthetic import write_pair

    from vascqbank_desktop.services import import_service
    from vascqbank_desktop.services.auto_backup import AutoBackupOutcome, run_automatic_backup

    source = write_pair(tmp_path / "src", "ALI", range(1, 6))[0].parent
    import_service.commit_import(paths, import_service.dry_run(paths, [source]))  # pre-import backup
    backup = create_manual_backup(paths).path
    assert backup_db.check_backup_file(backup).ok
    approve(paths, "ALI-0001")
    from datetime import UTC, datetime, timedelta

    automatic = run_automatic_backup(paths, now=datetime.now(UTC) + timedelta(days=2))
    assert automatic.outcome is AutoBackupOutcome.CREATED, automatic.detail
    restore_backup(paths, backup, expected_sha256=inspect_backup(backup).sha256)  # safety backup too
    assert prepare_storage(paths, None).outcome is Outcome.READY


def test_a_data_folder_beyond_the_path_limit_is_refused_before_anything_is_created(tmp_path: Path) -> None:
    paths = _folder_with_longest_path(tmp_path, LIMIT + 1)
    assert data_dir_too_long(paths)
    with pytest.raises(DataDirTooLong):
        prepare_storage(paths, None)
    assert not paths.data_dir.exists()


def test_a_path_longer_than_260_characters(tmp_path: Path) -> None:
    deep = tmp_path / "data folder with spaces"
    while len(str(deep)) < 300:
        deep = deep / "abcdefghijklmnopqrst"
    paths = AppPaths(data_dir=deep)
    if data_dir_too_long(paths):  # Windows: SQLite's MAX_PATH; refused cleanly with a Georgian message
        assert WINDOWS
        with pytest.raises(DataDirTooLong):
            prepare_storage(paths, None)
        assert not paths.data_dir.exists()
        from vascqbank_desktop.ui import strings

        assert "მისამართი ძალიან გრძელია" in strings.STARTUP_PATH_TOO_LONG
        return
    seed_counts(paths, {"ALI": 3})  # POSIX: 300 ASCII bytes are well inside SQLite's limit
    backup = create_manual_backup(paths).path
    approve(paths, "ALI-0001")
    restore_backup(paths, backup, expected_sha256=inspect_backup(backup).sha256)
    assert prepare_storage(paths, None).outcome is Outcome.READY


def test_self_check_reports_a_too_long_data_folder(tmp_path: Path, capsys) -> None:
    from vascqbank_desktop.app import main as app_main

    paths = _folder_with_longest_path(tmp_path, LIMIT + 1)
    assert app_main._run_self_check(paths, None) == app_main.EXIT_STORAGE_ERROR
    assert "DataDirTooLong" in capsys.readouterr().err or not paths.database_file.exists()
    assert not paths.database_file.exists()
