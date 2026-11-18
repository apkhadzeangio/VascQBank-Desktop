"""Conservative automatic start-up backup (Phase 9, ARCHITECTURE.md §37).

Policy, evaluated once per launch (in a background job, a few seconds after the window opens):

* no working database, or no questions in it → nothing to protect, no backup;
* the newest *verified* backup of this database (any kind: manual, automatic, before import,
  before restore, imported) is younger than 24 hours → no backup;
* that backup's manifest records the same :func:`change_signature` as the live database →
  nothing changed since, no backup (so an idle installation does not collect copies);
* otherwise one ``…-automatic.sqlite3`` backup is created and verified, then only the
  *automatic* backups beyond :data:`KEEP_DAILY` are pruned — manual, before-import,
  before-migration, before-restore and imported backups are never touched by this policy.

A failure is logged and reported, never raised: the reviewer's work must not be blocked by it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.db.connection import open_database

__all__ = [
    "MAX_AGE",
    "AutoBackupOutcome",
    "AutoBackupResult",
    "latest_verified_backup",
    "run_automatic_backup",
]

log = logging.getLogger(__name__)

MAX_AGE = timedelta(hours=24)
_VERIFY_AT_MOST = 5  # newest candidates verified before giving up (each check hashes the file)


class AutoBackupOutcome(StrEnum):
    CREATED = "CREATED"
    RECENT_BACKUP = "RECENT_BACKUP"
    UNCHANGED = "UNCHANGED"
    EMPTY_DATABASE = "EMPTY_DATABASE"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class AutoBackupResult:
    outcome: AutoBackupOutcome
    path: Path | None = None  # the new backup (CREATED) or the recent one that made it unnecessary
    pruned: tuple[Path, ...] = ()
    detail: str = ""


def _created_at(manifest: dict[str, object]) -> datetime | None:
    value = manifest.get("created_at")
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def latest_verified_backup(
    paths: AppPaths, database_id: str
) -> tuple[Path, dict[str, object], datetime] | None:
    """The newest backup of *database_id* that passes full verification (or ``None``)."""
    candidates: list[tuple[datetime, Path, dict[str, object]]] = []
    for path in backup_db.list_backups(paths.backups_dir):
        manifest = backup_db.read_manifest(path)
        if not manifest or manifest.get("database_id") != database_id:
            continue
        moment = _created_at(manifest)
        if moment is not None:
            candidates.append((moment, path, manifest))
    candidates.sort(key=lambda c: (c[0], c[1].name), reverse=True)
    for moment, path, manifest in candidates[:_VERIFY_AT_MOST]:
        if backup_db.check_backup_file(path).ok:
            return path, manifest, moment
    return None


def run_automatic_backup(
    paths: AppPaths, *, now: datetime | None = None, max_age: timedelta = MAX_AGE
) -> AutoBackupResult:
    """Apply the start-up policy once; never raises."""
    moment = now or datetime.now(UTC)
    try:
        # half-written files of a backup interrupted earlier (only exact names, only > 1 h old)
        for leftover in backup_db.remove_stale_leftovers(paths.backups_dir):
            log.info("Removed leftover of an interrupted backup: %s", leftover.name)
        if not paths.database_file.exists():
            return AutoBackupResult(AutoBackupOutcome.EMPTY_DATABASE)
        conn = open_database(paths.database_file)
        try:
            has_questions = (
                conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='questions'").fetchone()
                and conn.execute("SELECT 1 FROM questions LIMIT 1").fetchone()
            )
            if not has_questions:
                return AutoBackupResult(AutoBackupOutcome.EMPTY_DATABASE)
            row = conn.execute("SELECT value FROM app_metadata WHERE key='database_id'").fetchone()
            database_id = str(row[0]) if row else ""
            latest = latest_verified_backup(paths, database_id)
            if latest is not None:
                path, manifest, created = latest
                if moment - created < max_age:
                    return AutoBackupResult(AutoBackupOutcome.RECENT_BACKUP, path)
                if manifest.get("change_signature") == backup_db.change_signature(conn):
                    return AutoBackupResult(AutoBackupOutcome.UNCHANGED, path)
            info = backup_db.create_backup(conn, paths.backups_dir, backup_db.DAILY_REASON)
        finally:
            conn.close()
        pruned = backup_db.prune_automatic_backups(
            paths.backups_dir, backup_db.KEEP_DAILY, reasons=(backup_db.DAILY_REASON,)
        )
    except Exception as exc:  # noqa: BLE001 - reported, never allowed to disturb the reviewer
        log.warning("Automatic backup failed: %s", exc)
        return AutoBackupResult(AutoBackupOutcome.FAILED, detail=str(exc))
    log.info("Automatic backup created: %s (pruned %d)", info.path.name, len(pruned))
    return AutoBackupResult(AutoBackupOutcome.CREATED, info.path, tuple(pruned))
