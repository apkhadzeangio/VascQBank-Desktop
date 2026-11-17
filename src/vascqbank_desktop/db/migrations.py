"""Schema versioning and forward-only migrations.

Design
------
* ``PRAGMA user_version`` holds the current schema version (fast to read,
  stored in the database header).
* ``PRAGMA application_id`` stamps the file as a VascQBank database.
* ``schema_migrations`` records when each version was applied and by which
  application version (an audit trail, never used for control flow).
* ``app_metadata`` holds key/value facts about this database instance,
  including a random ``database_id`` that later appears in exports and
  backups to show which installation produced them.

Each migration runs inside one ``BEGIN IMMEDIATE`` transaction together with
the version bump, so a failure leaves the database exactly at its previous
version. Migrations are append-only: never edit a released one; add a new
version instead. Opening a database whose version is newer than
:data:`LATEST_VERSION` is refused rather than risking silent damage.
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from vascqbank_desktop import __version__
from vascqbank_desktop.config.constants import SQLITE_APPLICATION_ID
from vascqbank_desktop.db.errors import MigrationError, SchemaTooNewError
from vascqbank_desktop.db.identity import (
    ensure_owned_or_blank,
    get_schema_version,
    is_blank_database,
)

__all__ = [
    "LATEST_VERSION",
    "MIGRATIONS",
    "Migration",
    "MigrationResult",
    "get_schema_version",
    "migrate",
    "read_metadata",
]


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    apply: Callable[[sqlite3.Connection], None]


@dataclass(frozen=True, slots=True)
class MigrationResult:
    from_version: int
    to_version: int
    applied: tuple[int, ...] = field(default_factory=tuple)
    created_new: bool = False


def utc_now_iso() -> str:
    """UTC timestamp in ISO-8601 with an explicit offset, second precision."""
    return datetime.now(UTC).replace(microsecond=0).isoformat()


# --------------------------------------------------------------------------
# Migration 1 — schema-version foundation (Phase 1)
# --------------------------------------------------------------------------
def _m0001_foundation(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version      INTEGER PRIMARY KEY CHECK (version > 0),
            name         TEXT    NOT NULL,
            applied_at   TEXT    NOT NULL,
            app_version  TEXT    NOT NULL
        ) STRICT
        """
    )
    conn.execute(
        """
        CREATE TABLE app_metadata (
            key    TEXT PRIMARY KEY,
            value  TEXT NOT NULL
        ) STRICT, WITHOUT ROWID
        """
    )
    conn.executemany(
        "INSERT INTO app_metadata (key, value) VALUES (?, ?)",
        [
            ("database_id", str(uuid.uuid4())),
            ("created_at", utc_now_iso()),
            ("created_by_app_version", __version__),
        ],
    )


MIGRATIONS: tuple[Migration, ...] = (
    Migration(1, "schema-version foundation", _m0001_foundation),
)

LATEST_VERSION: int = MIGRATIONS[-1].version


# --------------------------------------------------------------------------
# Runner
# --------------------------------------------------------------------------
def _validate_migration_list(migrations: Sequence[Migration]) -> None:
    expected = list(range(1, len(migrations) + 1))
    if [m.version for m in migrations] != expected:
        raise ValueError("Migrations must be numbered consecutively from 1")


def migrate(
    conn: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> MigrationResult:
    """Bring the database up to the latest schema version.

    Raises :class:`ForeignDatabaseError` for databases of other programs,
    :class:`SchemaTooNewError` for databases from a newer app version and
    :class:`MigrationError` if a migration fails (after rolling back).
    """
    _validate_migration_list(migrations)
    latest = migrations[-1].version if migrations else 0

    # open_database() already checked this; repeated here because migrate()
    # must be safe on any connection it is handed.
    ensure_owned_or_blank(conn)
    current = get_schema_version(conn)
    is_empty = is_blank_database(conn)

    if current > latest:
        raise SchemaTooNewError(found=current, supported=latest)

    applied: list[int] = []
    for migration in migrations:
        if migration.version <= current:
            continue
        try:
            conn.execute("BEGIN IMMEDIATE")
            if migration.version == 1:
                conn.execute(f"PRAGMA application_id = {SQLITE_APPLICATION_ID}")
            migration.apply(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name, applied_at, app_version) "
                "VALUES (?, ?, ?, ?)",
                (migration.version, migration.name, utc_now_iso(), __version__),
            )
            # PRAGMA does not accept bound parameters; version is an int we control.
            conn.execute(f"PRAGMA user_version = {int(migration.version)}")
            conn.execute("COMMIT")
        except Exception as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise MigrationError(
                f"Migration {migration.version} ({migration.name}) failed: {exc}"
            ) from exc
        applied.append(migration.version)

    return MigrationResult(
        from_version=current,
        to_version=get_schema_version(conn),
        applied=tuple(applied),
        created_new=is_empty,
    )


def read_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT key, value FROM app_metadata ORDER BY key").fetchall()
    return {row[0]: row[1] for row in rows}
