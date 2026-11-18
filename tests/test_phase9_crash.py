"""Phase 9 interruption tests for the operations that had none: import commit, backup creation,
the automatic backup, "save backup as…" and adding an external backup.

Each scenario runs in a child process on a throw-away synthetic database in a temporary folder and
is terminated abruptly at a named point: ``SIGKILL`` on Linux/macOS, ``TerminateProcess`` (which
is what ``os.kill(pid, SIGTERM)`` does on Windows) elsewhere — neither lets Python clean up.
Afterwards the next start-up is simulated and the tests check: ``integrity_check``,
``foreign_key_check``, the exact expected committed state, that unrelated data is unchanged, that
no audit event was duplicated, and that repeating the recovery changes nothing.

These are process-kill tests. They say nothing about power loss (the OS cache may be lost then).
"""

from __future__ import annotations

import os
import signal
import sqlite3
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from bank import approve, seed_counts, snapshot
from synthetic import write_pair

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.services import import_service
from vascqbank_desktop.services.backup_service import create_manual_backup
from vascqbank_desktop.services.import_service import ImportAbortedError
from vascqbank_desktop.services.restore_service import RecoveryOutcome, recover_interrupted_restore
from vascqbank_desktop.services.startup import Outcome, prepare_storage

KILLED = (-signal.SIGKILL,) if os.name != "nt" else (signal.SIGTERM,)

CHILD = textwrap.dedent(
    """
    import os, signal, sys, time
    from pathlib import Path

    def die():
        os.kill(os.getpid(), signal.SIGTERM if os.name == "nt" else signal.SIGKILL)
        time.sleep(30)

    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.db import backup as backup_db
    from vascqbank_desktop.db import question_repository
    from vascqbank_desktop.services import auto_backup, backup_portability, backup_service, import_service

    paths = AppPaths(data_dir=Path(sys.argv[1]))
    scenario, point, extra = sys.argv[2], sys.argv[3], sys.argv[4]

    def kill_around(module, name, *, after, nth=1):
        real = getattr(module, name)
        calls = {"n": 0}
        def wrapper(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == nth and not after:
                die()
            result = real(*args, **kwargs)
            if calls["n"] == nth and after:
                die()
            return result
        setattr(module, name, wrapper)

    if point == "before-manifest":
        kill_around(backup_db, "_write_json_durably", after=False)
    elif point == "after-manifest":
        kill_around(backup_db, "_write_json_durably", after=True)
    elif point == "after-rename":
        kill_around(backup_db, "_fsync_dir", after=False)
    elif point == "after-backup":
        kill_around(import_service, "create_backup", after=True)
    elif point == "mid-insert":
        real_insert = question_repository.insert_import
        def insert(conn, *args, **kwargs):
            ticks = {"n": 0}
            def handler():
                ticks["n"] += 1
                if ticks["n"] >= int(extra):
                    die()
                return 0
            conn.set_progress_handler(handler, 100)
            return real_insert(conn, *args, **kwargs)
        question_repository.insert_import = insert
    elif point == "copy-before-first":
        kill_around(backup_portability, "_publish_no_overwrite", after=False, nth=1)
    elif point == "copy-after-first":
        kill_around(backup_portability, "_publish_no_overwrite", after=True, nth=1)

    if scenario == "manual-backup":
        backup_service.create_manual_backup(paths)
    elif scenario == "auto-backup":
        result = auto_backup.run_automatic_backup(paths)
        assert result.outcome.value != "FAILED", result
    elif scenario == "import":
        report = import_service.dry_run(paths, [Path(extra if point != "mid-insert" else sys.argv[5])])
        import_service.commit_import(paths, report)
    elif scenario == "save-copy":
        backup_portability.save_backup_copy(Path(sys.argv[5]), Path(extra))
    elif scenario == "add-external":
        backup_portability.import_external_backup(paths, Path(extra))
    print("SURVIVED", flush=True)
    """
)


def run_child(paths: AppPaths, scenario: str, point: str, *extra: str) -> bool:
    """Run the scenario; True if it finished (the kill point was never reached)."""
    args = [str(paths.data_dir), scenario, point, *(extra or ("-",))]
    child = subprocess.run(
        [sys.executable, "-c", CHILD, *args], capture_output=True, text=True, encoding="utf-8", timeout=180
    )
    if "SURVIVED" in child.stdout:
        assert child.returncode == 0, child.stderr
        return True
    assert child.returncode in KILLED, (child.returncode, child.stderr[-3000:])
    return False


def assert_sound(database: Path) -> None:
    conn = sqlite3.connect(database)
    try:
        assert [r[0] for r in conn.execute("PRAGMA integrity_check")] == ["ok"]
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        conn.close()


def start_up(paths: AppPaths) -> None:
    """What the next launch does first; it must succeed and be repeatable without changes."""
    result = prepare_storage(paths, None)
    assert result.outcome is Outcome.READY, result
    assert result.status is not None and result.status.integrity_ok
    assert recover_interrupted_restore(paths).outcome is RecoveryOutcome.NONE
    assert_sound(paths.database_file)


def listed_backups_all_verify(paths: AppPaths) -> list[Path]:
    """A backup that is visible must be complete: manifest present and every check passing."""
    listed = backup_db.list_backups(paths.backups_dir)
    for path in listed:
        check = backup_db.check_backup_file(path)
        assert check.ok and check.has_manifest, (path.name, check)
    return listed


def count(paths: AppPaths, sql: str) -> int:
    conn = sqlite3.connect(paths.database_file)
    try:
        return int(conn.execute(sql).fetchone()[0])
    finally:
        conn.close()


@pytest.fixture
def bank(app_paths: AppPaths) -> AppPaths:
    seed_counts(app_paths, {"VENOUS": 6, "PAD_CLTI": 4})
    approve(app_paths, "VENOUS-0002")
    return app_paths


# --------------------------------------------------------------------------------------- backups
@pytest.mark.parametrize("scenario", ["manual-backup", "auto-backup"])
@pytest.mark.parametrize("point", ["before-manifest", "after-manifest", "after-rename"])
def test_a_backup_killed_at_any_point_is_absent_or_complete(
    bank: AppPaths, scenario: str, point: str
) -> None:
    before = snapshot(bank)
    survived = run_child(bank, scenario, point)
    assert not survived

    start_up(bank)
    assert snapshot(bank) == before  # a backup only reads the working database
    listed = listed_backups_all_verify(bank)
    # Before the rename nothing is visible; after it the backup is complete (manifest came first).
    assert len(listed) == (1 if point == "after-rename" else 0)
    # Leftovers are hidden from the list and cleaned up once they are stale; a new backup works.
    assert create_manual_backup(bank).path.exists()
    removed = backup_db.remove_stale_leftovers(bank.backups_dir, now=float(10**12))
    assert all(p.name.endswith((".partial", ".tmp")) for p in removed)
    assert len(listed_backups_all_verify(bank)) == len(listed) + 1


# ---------------------------------------------------------------------------------------- import
@pytest.fixture
def import_folder(tmp_path: Path) -> Path:
    folder = tmp_path / "სინთეზური იმპორტი"
    write_pair(folder, "ALI", range(1, 31))
    return folder


def _import_again(paths: AppPaths, folder: Path) -> None:
    report = import_service.dry_run(paths, [folder])
    try:
        import_service.commit_import(paths, report)
    except ImportAbortedError:  # everything was imported already
        return


def test_import_killed_after_its_backup_imports_nothing(bank: AppPaths, import_folder: Path) -> None:
    before = snapshot(bank)
    assert not run_child(bank, "import", "after-backup", str(import_folder))
    start_up(bank)
    assert snapshot(bank) == before
    listed = listed_backups_all_verify(bank)
    assert [backup_db.backup_reason(p) for p in listed] == ["pre-import"]
    _import_again(bank, import_folder)
    assert count(bank, "SELECT count(*) FROM questions WHERE subject_code='ALI'") == 30


@pytest.mark.parametrize("ticks", [1, 5, 25, 120])
def test_import_killed_inside_its_transaction_is_all_or_nothing(
    bank: AppPaths, import_folder: Path, ticks: int
) -> None:
    events = count(bank, "SELECT count(*) FROM review_events")
    unrelated = {
        "VENOUS": count(bank, "SELECT count(*) FROM questions WHERE subject_code='VENOUS'"),
        "approved": count(bank, "SELECT count(*) FROM questions WHERE status='APPROVED'"),
    }
    survived = run_child(bank, "import", "mid-insert", str(ticks), str(import_folder))
    start_up(bank)

    imported = count(bank, "SELECT count(*) FROM questions WHERE subject_code='ALI'")
    batches = count(bank, "SELECT count(*) FROM import_batches")
    assert imported in (0, 30)  # one transaction: never a partial batch
    assert batches == (2 if imported else 1)
    if survived:
        assert imported == 30
    assert count(bank, "SELECT count(*) FROM question_revisions") == 10 + imported
    assert count(bank, "SELECT count(*) FROM review_events") == events  # nothing duplicated
    assert count(bank, "SELECT count(*) FROM questions WHERE subject_code='VENOUS'") == unrelated["VENOUS"]
    assert count(bank, "SELECT count(*) FROM questions WHERE status='APPROVED'") == unrelated["approved"]
    listed_backups_all_verify(bank)

    _import_again(bank, import_folder)  # repeating the import is idempotent
    _import_again(bank, import_folder)
    assert count(bank, "SELECT count(*) FROM questions WHERE subject_code='ALI'") == 30
    assert count(bank, "SELECT count(*) FROM question_revisions") == 40
    start_up(bank)


# ------------------------------------------------------------------------------- portable copies
@pytest.mark.parametrize("point", ["copy-before-first", "copy-after-first"])
def test_save_copy_killed_never_touches_the_original_and_never_overwrites(
    bank: AppPaths, tmp_path: Path, point: str
) -> None:
    source = create_manual_backup(bank).path
    original = source.read_bytes()
    usb = tmp_path / "usb"
    usb.mkdir()
    assert not run_child(bank, "save-copy", point, str(usb), str(source))

    assert source.read_bytes() == original and backup_db.check_backup_file(source).ok
    visible = sorted(p.name for p in usb.iterdir() if not p.name.startswith("."))
    if point == "copy-before-first":
        assert visible == []
    else:  # the database copy is complete, its manifest was never written: it does not verify
        assert visible == [source.name]
        copy = usb / source.name
        assert copy.read_bytes() == original
        assert not backup_db.check_backup_file(copy).has_manifest
    # Saving again into the same folder refuses rather than overwrite the earlier attempt.
    from vascqbank_desktop.services.backup_portability import BackupCopyError, CopyProblem, save_backup_copy

    if visible:
        with pytest.raises(BackupCopyError) as caught:
            save_backup_copy(source, usb)
        assert caught.value.problem is CopyProblem.DESTINATION_EXISTS
    else:
        assert save_backup_copy(source, usb).backup.exists()
    start_up(bank)


def test_adding_an_external_backup_killed_leaves_no_half_backup(bank: AppPaths, tmp_path: Path) -> None:
    from vascqbank_desktop.services.backup_portability import import_external_backup, save_backup_copy

    source = create_manual_backup(bank).path
    usb = tmp_path / "usb"
    usb.mkdir()
    external = save_backup_copy(source, usb).backup
    source.unlink()
    source.with_name(source.name + ".json").unlink()
    # nth=1 is the manifest (written first), so the kill comes before the database copy.
    assert not run_child(bank, "add-external", "copy-after-first", str(external))
    assert listed_backups_all_verify(bank) == []  # an orphan manifest is never listed as a backup
    added = import_external_backup(bank, external)
    assert listed_backups_all_verify(bank) == [added.backup]
    start_up(bank)


def test_the_import_commit_is_synced_like_reviewer_work(
    bank: AppPaths, import_folder: Path, monkeypatch
) -> None:
    """Phase 9 audit finding: the import committed with WAL's default ``synchronous=NORMAL``."""
    levels: list[int] = []
    real = import_service.make_durable

    def spy(conn: sqlite3.Connection) -> sqlite3.Connection:
        result = real(conn)
        levels.append(conn.execute("PRAGMA synchronous").fetchone()[0])
        return result

    monkeypatch.setattr(import_service, "make_durable", spy)
    import_service.commit_import(bank, import_service.dry_run(bank, [import_folder]))
    assert levels == [2]  # FULL on the connection that writes the batch
