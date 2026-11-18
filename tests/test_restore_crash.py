"""A restore killed (SIGKILL) at every stage, and the start-up recovery that resolves it.

Every child process works on a throw-away synthetic database in a temporary folder.
"""

from __future__ import annotations

import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest
from bank import approve, seed_counts, simulate_edit
from test_restore_service import questions

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.services import restore_service
from vascqbank_desktop.services.backup_service import create_manual_backup
from vascqbank_desktop.services.restore_service import (
    RecoveryOutcome,
    journal_path,
    recover_interrupted_restore,
)
from vascqbank_desktop.services.seed_service import sha256_file
from vascqbank_desktop.services.startup import Outcome, prepare_storage

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL")

CHILD = textwrap.dedent(
    """
    import os, signal, sys
    from pathlib import Path
    from vascqbank_desktop.config.paths import AppPaths
    from vascqbank_desktop.services import restore_service

    stage = sys.argv[3]
    def checkpoint(name):
        if name == stage:
            os.kill(os.getpid(), signal.SIGKILL)
    restore_service._checkpoint = checkpoint
    paths = AppPaths(data_dir=Path(sys.argv[1]))
    backup = Path(sys.argv[2])
    if sys.argv[4] == "restore":
        restore_service.restore_backup(paths, backup, expected_sha256=restore_service.sha256_file(backup))
    else:
        restore_service.recover_interrupted_restore(paths)
    print("SURVIVED", flush=True)
    """
)


def kill_at(paths: AppPaths, backup: Path, stage: str, action: str = "restore") -> None:
    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(paths.data_dir), str(backup), stage, action],
        capture_output=True, text=True, timeout=120,
    )  # fmt: skip
    assert child.returncode == -signal.SIGKILL and "SURVIVED" not in child.stdout, child.stderr


@pytest.fixture
def setup(app_paths: AppPaths):
    seed_counts(app_paths, {"ALI": 5, "VENOUS": 3})
    approve(app_paths, "ALI-0001")
    backup = create_manual_backup(app_paths).path
    in_backup = questions(app_paths)
    approve(app_paths, "VENOUS-0002")
    simulate_edit(app_paths, "ALI-0003", stem="ასლის შემდეგ დაწერილი ტექსტი")
    later = questions(app_paths)
    return app_paths, backup, in_backup, later


EXPECTED = {
    # stage reached when killed -> (recovery outcome, which content the working database then has)
    "verified": (RecoveryOutcome.NONE, "later"),
    "safety-backup": (RecoveryOutcome.NONE, "later"),
    "temp-created": (RecoveryOutcome.NONE, "later"),
    "journal-written": (RecoveryOutcome.ROLLED_BACK, "later"),
    "displaced": (RecoveryOutcome.ROLLED_BACK, "later"),
    "replaced": (RecoveryOutcome.COMPLETED, "backup"),
    "published": (RecoveryOutcome.COMPLETED, "backup"),
    "index-removed": (RecoveryOutcome.COMPLETED, "backup"),
    "completed": (RecoveryOutcome.NONE, "backup"),
}


@pytest.mark.parametrize("stage", list(EXPECTED))
def test_a_restore_killed_at_any_stage_is_resolved_at_start_up(setup, stage: str) -> None:
    paths, backup, in_backup, later = setup
    original = sha256_file(paths.database_file)
    paths.search_index_file.write_bytes(b"derived index")
    kill_at(paths, backup, stage)

    outcome, content = EXPECTED[stage]
    result = prepare_storage(paths, None)  # what the next start does first
    assert result.outcome is Outcome.READY, result
    got = result.restore_recovery.outcome if result.restore_recovery else RecoveryOutcome.NONE
    assert got is outcome
    assert questions(paths) == (in_backup if content == "backup" else later)
    if content == "later":
        assert sha256_file(paths.database_file) == original  # not one byte of the newer work changed
    else:
        assert not paths.search_index_file.exists()  # the stale index of the replaced data is gone
    # nothing is left half-done, and running the recovery again changes nothing
    assert not journal_path(paths).exists() and not restore_service.stray_temporary_files(paths)
    assert recover_interrupted_restore(paths).outcome is RecoveryOutcome.NONE
    # whatever happened, the work done after the backup is never lost
    safety = sorted(paths.backups_dir.glob("vascqbank-*-pre-restore.sqlite3"))
    if content == "backup":
        assert len(safety) == 1
        displaced = sorted((paths.data_dir / "displaced").glob("*-displaced.sqlite3"))
        assert [sha256_file(p) for p in displaced] == [original]


def test_a_missing_database_restore_killed_before_publishing_leaves_no_trace(app_paths: AppPaths) -> None:
    seed_counts(app_paths, {"ALI": 4})
    backup = create_manual_backup(app_paths).path
    app_paths.database_file.unlink()
    kill_at(app_paths, backup, "displaced")
    result = prepare_storage(app_paths, None)
    assert result.restore_recovery and result.restore_recovery.outcome is RecoveryOutcome.ROLLED_BACK
    assert result.outcome is Outcome.RECOVERY_NEEDED and not app_paths.database_file.exists()  # still asked


def test_a_crash_during_the_recovery_itself_is_recovered_again(setup) -> None:
    paths, backup, in_backup, _later = setup
    kill_at(paths, backup, "published")
    kill_at(paths, backup, "index-removed", action="recover")  # the recovery is interrupted too
    assert journal_path(paths).exists()
    assert recover_interrupted_restore(paths).outcome is RecoveryOutcome.COMPLETED
    assert questions(paths) == in_backup
