"""Is a database's schema exactly the one our migrations create? (Phase 9b)

A backup, a seed or an external backup is a *file from outside*: its ``sqlite_schema`` could hold
objects the application never created — an extra ``TRIGGER`` (which would run inside the
application's own transactions after a restore), a ``VIEW``, an ``INDEX``, or a table definition
with a ``CHECK`` or ``FOREIGN KEY`` removed. ``integrity_check`` and ``foreign_key_check`` do not see
any of that, because such a database is perfectly *consistent* — just not ours.

:func:`schema_differences` compares every schema object (type, name, table and the exact ``CREATE``
statement) with the schema that :data:`~vascqbank_desktop.db.migrations.MIGRATIONS` produce for
the file's ``user_version``, built once per version in an in-memory database. Released migrations
are never edited (ARCHITECTURE.md §4.3), so every database this application ever wrote matches
exactly; the upgrade tests (``tests/test_upgrade_history.py``) prove it for every earlier release.

Only reading statements are run on the inspected connection; nothing is written.
"""

from __future__ import annotations

import sqlite3
from functools import cache

from vascqbank_desktop.db.migrations import MIGRATIONS, migrate

SchemaObject = tuple[str, str, str, str | None]  # (type, name, tbl_name, sql)

# Objects SQLite itself creates on demand. ``sqlite_sequence`` appears with the first AUTOINCREMENT
# table and is part of the migrated schema anyway; it is still compared like any other object.
_QUERY = "SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name"


def schema_objects(conn: sqlite3.Connection) -> frozenset[SchemaObject]:
    return frozenset(
        (str(row[0]), str(row[1]), str(row[2]), None if row[3] is None else str(row[3]))
        for row in conn.execute(_QUERY)
    )


@cache
def expected_schema(version: int) -> frozenset[SchemaObject]:
    """The schema objects that migrations ``1 … version`` create (cached per version)."""
    if not 0 <= version <= len(MIGRATIONS):
        raise ValueError(f"no migrations for schema version {version}")
    conn = sqlite3.connect(":memory:", isolation_level=None)
    try:
        if version:
            migrate(conn, MIGRATIONS[:version])
        return schema_objects(conn)
    finally:
        conn.close()


def _describe(obj: SchemaObject) -> str:
    kind, name, table, _sql = obj
    return f"{kind} {name}" + (f" (on {table})" if table != name else "")


def schema_differences(conn: sqlite3.Connection, version: int, *, limit: int = 5) -> list[str]:
    """Why the schema behind *conn* is not exactly migration *version*'s (empty list = identical)."""
    try:
        expected = expected_schema(version)
    except ValueError as exc:
        return [str(exc)]
    actual = schema_objects(conn)
    expected_by_key = {(o[0], o[1]): o for o in expected}
    actual_by_key = {(o[0], o[1]): o for o in actual}
    problems: list[str] = []
    for key in sorted(actual_by_key.keys() - expected_by_key.keys()):
        problems.append("unexpected " + _describe(actual_by_key[key]))
    for key in sorted(expected_by_key.keys() - actual_by_key.keys()):
        problems.append("missing " + _describe(expected_by_key[key]))
    for key in sorted(actual_by_key.keys() & expected_by_key.keys()):
        if actual_by_key[key] != expected_by_key[key]:
            problems.append("changed " + _describe(actual_by_key[key]))
    return problems[:limit] if limit else problems
