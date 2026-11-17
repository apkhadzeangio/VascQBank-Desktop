"""Storage initialisation use case."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.connection import has_fts5_trigram, open_database, quick_check
from vascqbank_desktop.db.migrations import LATEST_VERSION, migrate, read_metadata

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class StorageStatus:
    """Facts about local storage, safe to hand to the UI."""

    data_dir: Path
    database_path: Path
    schema_version: int
    latest_schema_version: int
    created_new: bool
    applied_migrations: tuple[int, ...]
    database_id: str
    integrity_ok: bool
    sqlite_version: str
    fts5_trigram: bool


def initialize_storage(paths: AppPaths) -> StorageStatus:
    """Create directories, open the database, migrate it and report status.

    The connection is closed before returning; Phase 2 introduces a
    long-lived repository/unit-of-work for question data.
    """
    paths.ensure_directories()
    conn = open_database(paths.database_file)
    try:
        result = migrate(conn)
        if result.applied:
            log.info(
                "Database migrated from v%s to v%s (%s)",
                result.from_version,
                result.to_version,
                paths.database_file,
            )
        metadata = read_metadata(conn)
        status = StorageStatus(
            data_dir=paths.data_dir,
            database_path=paths.database_file,
            schema_version=result.to_version,
            latest_schema_version=LATEST_VERSION,
            created_new=result.created_new,
            applied_migrations=result.applied,
            database_id=metadata.get("database_id", ""),
            integrity_ok=quick_check(conn),
            sqlite_version=sqlite3.sqlite_version,
            fts5_trigram=has_fts5_trigram(conn),
        )
    finally:
        conn.close()
    if not status.integrity_ok:
        log.error("SQLite quick_check reported problems for %s", paths.database_file)
    return status
