"""Database ownership checks.

These run *before* any persistent setting (such as WAL mode) is applied, so a
SQLite file that belongs to another program is never modified, not even in its
header.
"""

from __future__ import annotations

import sqlite3

from vascqbank_desktop.config.constants import SQLITE_APPLICATION_ID
from vascqbank_desktop.db.errors import ForeignDatabaseError


def get_application_id(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA application_id").fetchone()[0])


def get_schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def user_object_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT count(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
    ).fetchone()
    return int(row[0])


def is_blank_database(conn: sqlite3.Connection) -> bool:
    """A brand-new (or zero-length) file: no schema, no version, no stamp."""
    return (
        get_application_id(conn) == 0
        and get_schema_version(conn) == 0
        and user_object_count(conn) == 0
    )


def ensure_owned_or_blank(conn: sqlite3.Connection) -> None:
    """Raise :class:`ForeignDatabaseError` unless the file is ours or blank."""
    app_id = get_application_id(conn)
    if app_id == SQLITE_APPLICATION_ID or is_blank_database(conn):
        return
    raise ForeignDatabaseError(
        "This SQLite file was not created by VascQBank Review Desktop "
        f"(application_id={app_id:#010x}); it will not be modified."
    )
