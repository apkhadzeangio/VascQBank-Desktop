from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

import pytest

from vascqbank_desktop.config.constants import SQLITE_APPLICATION_ID
from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.connection import (
    ensure_supported_sqlite,
    has_fts5_trigram,
    open_database,
    quick_check,
)
from vascqbank_desktop.db.errors import (
    DatabaseError,
    ForeignDatabaseError,
    MigrationError,
    SchemaTooNewError,
)
from vascqbank_desktop.db.migrations import (
    LATEST_VERSION,
    MIGRATIONS,
    Migration,
    get_schema_version,
    migrate,
    read_metadata,
)
from vascqbank_desktop.services.storage import initialize_storage


@pytest.fixture
def db_path(data_dir: Path) -> Path:
    path = data_dir / "database" / "test.sqlite3"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@pytest.fixture
def conn(db_path: Path):
    connection = open_database(db_path)
    yield connection
    connection.close()


def test_new_database_is_migrated_to_latest(conn: sqlite3.Connection) -> None:
    result = migrate(conn)
    assert result.created_new
    assert result.from_version == 0
    assert result.to_version == LATEST_VERSION == get_schema_version(conn)
    assert result.applied == tuple(m.version for m in MIGRATIONS)
    assert conn.execute("PRAGMA application_id").fetchone()[0] == SQLITE_APPLICATION_ID

    rows = conn.execute("SELECT version, name, app_version FROM schema_migrations").fetchall()
    assert [r["version"] for r in rows] == [1]
    meta = read_metadata(conn)
    assert set(meta) == {"database_id", "created_at", "created_by_app_version"}
    uuid.UUID(meta["database_id"])  # valid UUID


def test_connection_pragmas(conn: sqlite3.Connection) -> None:
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA encoding").fetchone()[0] == "UTF-8"
    assert conn.isolation_level is None


def test_migrate_is_idempotent(conn: sqlite3.Connection) -> None:
    first = migrate(conn)
    database_id = read_metadata(conn)["database_id"]
    second = migrate(conn)
    assert second.applied == ()
    assert not second.created_new
    assert second.from_version == second.to_version == first.to_version
    assert read_metadata(conn)["database_id"] == database_id


def test_georgian_text_round_trip(conn: sqlite3.Connection) -> None:
    migrate(conn)
    text = "ა) ბ) *გ) დ) ე) — „თეძოს საერთო არტერია“, ×10⁹/ლ, 0,9"
    conn.execute("INSERT INTO app_metadata (key, value) VALUES ('probe', ?)", (text,))
    stored = conn.execute("SELECT value, length(value) FROM app_metadata WHERE key='probe'").fetchone()
    assert stored[0] == text
    assert stored[1] == len(text)  # SQLite counts characters, not bytes


def test_tables_are_strict(conn: sqlite3.Connection) -> None:
    migrate(conn)
    rows = conn.execute(
        "SELECT name, strict FROM pragma_table_list WHERE schema='main' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    assert {r["name"]: r["strict"] for r in rows} == {"schema_migrations": 1, "app_metadata": 1}


def test_refuses_database_from_newer_app(db_path: Path) -> None:
    raw = sqlite3.connect(db_path)
    raw.execute(f"PRAGMA application_id = {SQLITE_APPLICATION_ID}")
    raw.execute("CREATE TABLE future_table (x)")
    raw.execute("PRAGMA user_version = 99")
    raw.commit()
    raw.close()

    conn = open_database(db_path)
    try:
        with pytest.raises(SchemaTooNewError) as info:
            migrate(conn)
        assert info.value.found == 99
        assert get_schema_version(conn) == 99  # untouched
    finally:
        conn.close()


def _make_foreign_db(path: Path, app_id: int) -> bytes:
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA application_id = {app_id}")
    raw.execute("CREATE TABLE someone_elses_data (x)")
    raw.commit()
    raw.close()
    return path.read_bytes()


@pytest.mark.parametrize("foreign_app_id", [0, 0x12345678])
def test_open_refuses_foreign_database_without_touching_it(
    db_path: Path, foreign_app_id: int
) -> None:
    before = _make_foreign_db(db_path, foreign_app_id)
    with pytest.raises(ForeignDatabaseError):
        open_database(db_path)
    # Byte-for-byte identical: not even the journal-mode header byte changed.
    assert db_path.read_bytes() == before
    assert not Path(f"{db_path}-wal").exists()


def test_migrate_also_refuses_foreign_database(db_path: Path) -> None:
    _make_foreign_db(db_path, 0x12345678)
    raw = sqlite3.connect(db_path, isolation_level=None)
    try:
        with pytest.raises(ForeignDatabaseError):
            migrate(raw)
        assert get_schema_version(raw) == 0
    finally:
        raw.close()


def test_open_rejects_non_sqlite_file(db_path: Path) -> None:
    db_path.write_bytes("ეს არ არის მონაცემთა ბაზა".encode() * 100)
    before = db_path.read_bytes()
    with pytest.raises(sqlite3.DatabaseError):
        open_database(db_path)
    assert db_path.read_bytes() == before


def test_failed_migration_rolls_back(conn: sqlite3.Connection) -> None:
    def broken(c: sqlite3.Connection) -> None:
        c.execute("CREATE TABLE half_done (x)")
        raise RuntimeError("simulated failure")

    migrate(conn)
    with pytest.raises(MigrationError):
        migrate(conn, migrations=[*MIGRATIONS, Migration(2, "broken", broken)])
    assert get_schema_version(conn) == 1
    assert not conn.in_transaction
    assert conn.execute("SELECT count(*) FROM sqlite_schema WHERE name='half_done'").fetchone()[0] == 0
    assert [r[0] for r in conn.execute("SELECT version FROM schema_migrations")] == [1]


def test_migration_list_must_be_consecutive(conn: sqlite3.Connection) -> None:
    gap = [*MIGRATIONS, Migration(3, "gap", lambda c: None)]
    with pytest.raises(ValueError):
        migrate(conn, migrations=gap)


def test_minimum_sqlite_version_guard() -> None:
    ensure_supported_sqlite((3, 37, 0))
    with pytest.raises(DatabaseError):
        ensure_supported_sqlite((3, 36, 9))


def test_health_probes(conn: sqlite3.Connection) -> None:
    migrate(conn)
    assert quick_check(conn)
    has_fts5_trigram(conn)  # result depends on the SQLite build; must not raise
    assert conn.execute("SELECT count(*) FROM temp.sqlite_schema").fetchone()[0] == 0


def test_initialize_storage_end_to_end(app_paths: AppPaths) -> None:
    first = initialize_storage(app_paths)
    assert first.created_new
    assert first.schema_version == first.latest_schema_version == LATEST_VERSION
    assert first.integrity_ok
    assert first.database_path == app_paths.database_file
    assert app_paths.database_file.is_file()
    assert app_paths.backups_dir.is_dir() and app_paths.exports_dir.is_dir()

    second = initialize_storage(app_paths)
    assert not second.created_new
    assert second.applied_migrations == ()
    assert second.database_id == first.database_id
