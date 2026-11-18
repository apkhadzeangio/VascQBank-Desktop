"""Backups for the UI: create a verified backup, list them, re-verify them.

Restoring a backup is :mod:`vascqbank_desktop.services.restore_service` (Phase 8): it has its own
safety backup, connection hand-over and crash-recovery protocol (ARCHITECTURE.md §28).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.db.backup import BackupProblem
from vascqbank_desktop.db.connection import open_database

__all__ = [
    "BackupEntry",
    "BackupKind",
    "BackupProblem",  # part of BackupEntry; re-exported so the UI never imports the db layer
    "VerificationStatus",
    "create_manual_backup",
    "list_backup_entries",
    "verify_all",
    "verify_entry",
]

log = logging.getLogger(__name__)

MANUAL_REASON = "manual"
_NAME = re.compile(r"^vascqbank-(\d{8})-(\d{6})-(\d{6})-(?P<reason>.+)\.sqlite3$")


class BackupKind(StrEnum):
    MANUAL = "MANUAL"
    BEFORE_IMPORT = "BEFORE_IMPORT"
    BEFORE_MIGRATION = "BEFORE_MIGRATION"
    BEFORE_RESTORE = "BEFORE_RESTORE"  # the safety backup taken before a restore; never pruned
    AUTOMATIC = "AUTOMATIC"  # Phase 9: the daily start-up backup (own retention, see auto_backup)
    IMPORTED = "IMPORTED"  # Phase 9: copied in from an external location; never pruned
    OTHER = "OTHER"


class VerificationStatus(StrEnum):
    UNCHECKED = "UNCHECKED"
    VERIFIED = "VERIFIED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class BackupEntry:
    path: Path
    kind: BackupKind
    created_at: str | None  # UTC ISO-8601
    size: int
    status: VerificationStatus = VerificationStatus.UNCHECKED
    problem: BackupProblem = BackupProblem.NONE
    detail: str = ""
    has_manifest: bool | None = None
    question_count: int | None = None
    schema_version: int | None = None

    @property
    def name(self) -> str:
        return self.path.name


def _kind(reason: str) -> BackupKind:
    if reason == MANUAL_REASON:
        return BackupKind.MANUAL
    if reason.startswith("pre-import"):
        return BackupKind.BEFORE_IMPORT
    if reason.startswith("pre-migration"):
        return BackupKind.BEFORE_MIGRATION
    if reason.startswith("pre-restore"):
        return BackupKind.BEFORE_RESTORE
    if reason == backup_db.DAILY_REASON:
        return BackupKind.AUTOMATIC
    if reason == "imported":
        return BackupKind.IMPORTED
    return BackupKind.OTHER


def _created_from_name(name: str) -> str | None:
    match = _NAME.match(name)
    if match is None:
        return None
    try:
        moment = datetime.strptime(f"{match[1]}{match[2]}", "%Y%m%d%H%M%S").replace(tzinfo=UTC)
    except ValueError:
        return None
    return moment.isoformat()


def _entry(path: Path) -> BackupEntry:
    match = _NAME.match(path.name)
    manifest = backup_db.read_manifest(path)
    created = manifest.get("created_at") if manifest else None
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    counts = manifest.get("table_counts") if manifest else None
    questions = counts.get("questions") if isinstance(counts, dict) else None
    return BackupEntry(
        path=path,
        kind=_kind(match["reason"]) if match else BackupKind.OTHER,
        created_at=created if isinstance(created, str) else _created_from_name(path.name),
        size=size,
        has_manifest=manifest is not None,
        # as recorded in the manifest (shown before verification; replaced by the verified count)
        question_count=questions if isinstance(questions, int) else None,
    )


def list_backup_entries(paths: AppPaths) -> list[BackupEntry]:
    """All backups, newest first. Fast: nothing is verified (status ``UNCHECKED``)."""
    entries = [_entry(p) for p in backup_db.list_backups(paths.backups_dir)]
    # File names start with a UTC timestamp, so name order is creation order.
    return sorted(entries, key=lambda e: e.name, reverse=True)


def verify_entry(entry: BackupEntry) -> BackupEntry:
    """Re-verify one backup: size, SHA-256, integrity check and row counts (read-only)."""
    check = backup_db.check_backup_file(entry.path)
    counts = check.table_counts or {}
    try:
        size = entry.path.stat().st_size
    except OSError:
        size = 0
    return replace(
        entry,
        size=size,
        status=VerificationStatus.VERIFIED if check.ok else VerificationStatus.FAILED,
        problem=check.problem,
        detail=check.message,
        has_manifest=check.has_manifest,
        question_count=counts.get("questions"),
        schema_version=check.schema_version,
    )


def verify_all(
    entries: Sequence[BackupEntry],
    progress: Callable[[int, BackupEntry], None] | None = None,
) -> list[BackupEntry]:
    """Verify every entry in turn, reporting each result as soon as it is known."""
    results: list[BackupEntry] = []
    for index, entry in enumerate(entries):
        result = verify_entry(entry)
        results.append(result)
        if progress is not None:
            progress(index, result)
    return results


def create_manual_backup(paths: AppPaths) -> BackupEntry:
    """Create and verify a backup now. Manual backups are never pruned.

    Raises :class:`vascqbank_desktop.db.backup.BackupError` if the backup could
    not be created or failed verification (nothing is left behind then).
    """
    conn = open_database(paths.database_file)
    try:
        info = backup_db.create_backup(conn, paths.backups_dir, MANUAL_REASON)
    finally:
        conn.close()
    log.info("Manual backup created: %s", info.path)
    return replace(
        _entry(info.path),
        status=VerificationStatus.VERIFIED,
        has_manifest=True,
        question_count=info.table_counts.get("questions"),
        schema_version=info.schema_version,
    )
