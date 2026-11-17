"""Verified database backups using SQLite's online backup API.

``sqlite3.Connection.backup`` copies a consistent snapshot even while the
database is open in WAL mode; the live file is never copied byte-wise.

Every backup is:

1. written to a ``.partial`` file next to its final name;
2. switched to rollback-journal mode, so it is one self-contained file;
3. verified — reopened read-only, application id, schema version,
   ``PRAGMA integrity_check`` and row counts checked;
4. renamed atomically to ``vascqbank-<UTC timestamp>-<reason>.sqlite3``;
5. described by a JSON manifest beside it (SHA-256, size, counts, database id).

If any step fails, nothing is left behind and :class:`BackupError` is raised —
callers (imports, migrations) must then refuse to continue.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from vascqbank_desktop import __version__
from vascqbank_desktop.config.constants import SQLITE_APPLICATION_ID
from vascqbank_desktop.db.errors import DatabaseError

AUTOMATIC_REASONS = ("pre-import", "pre-migration")
KEEP_AUTOMATIC = 20


class BackupError(DatabaseError):
    """A backup could not be created or failed verification."""


@dataclass(frozen=True, slots=True)
class BackupInfo:
    path: Path
    manifest_path: Path
    reason: str
    created_at: str
    sha256: str
    size: int
    schema_version: int
    database_id: str
    table_counts: dict[str, int]


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    ]
    return {name: int(conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0]) for name in names}


def verify_backup(path: Path) -> tuple[int, str, dict[str, int]]:
    """Open *path* read-only and check it; return (schema version, database id, counts)."""
    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise BackupError(f"Backup cannot be opened: {exc}") from exc
    try:
        app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
        if app_id != SQLITE_APPLICATION_ID:
            raise BackupError(f"Backup has a foreign application id {app_id:#010x}")
        result = [r[0] for r in conn.execute("PRAGMA integrity_check")]
        if result != ["ok"]:
            raise BackupError("Backup failed integrity_check: " + "; ".join(result[:5]))
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        database_id = ""
        if conn.execute("SELECT 1 FROM sqlite_schema WHERE name='app_metadata'").fetchone():
            row = conn.execute("SELECT value FROM app_metadata WHERE key='database_id'").fetchone()
            database_id = row[0] if row else ""
        return version, database_id, _table_counts(conn)
    except sqlite3.Error as exc:
        raise BackupError(f"Backup verification failed: {exc}") from exc
    finally:
        conn.close()


def _slug(reason: str) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", reason.lower()).strip("-")
    return slug or "manual"


def create_backup(conn: sqlite3.Connection, backups_dir: Path, reason: str) -> BackupInfo:
    """Back up the database behind *conn* into *backups_dir* and verify it."""
    backups_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%d-%H%M%S-%f")
    reason_slug = _slug(reason)
    final = backups_dir / f"vascqbank-{stamp}-{reason_slug}.sqlite3"
    partial = final.with_name(final.name + ".partial")
    manifest_path = final.with_name(final.name + ".json")

    expected_counts = _table_counts(conn)
    try:
        target = sqlite3.connect(partial)
        try:
            conn.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")
        finally:
            target.close()
        version, database_id, counts = verify_backup(partial)
        if counts != expected_counts:
            raise BackupError(f"Backup row counts differ: {counts} != {expected_counts}")
        data = partial.read_bytes()
        partial.replace(final)
    except BaseException:
        partial.unlink(missing_ok=True)
        raise

    info = BackupInfo(
        path=final,
        manifest_path=manifest_path,
        reason=reason_slug,
        created_at=now.replace(microsecond=0).isoformat(),
        sha256=hashlib.sha256(data).hexdigest(),
        size=len(data),
        schema_version=version,
        database_id=database_id,
        table_counts=counts,
    )
    manifest = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(info).items()}
    manifest["path"] = final.name
    manifest["manifest_path"] = manifest_path.name
    manifest["app_version"] = __version__
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def list_backups(backups_dir: Path) -> list[Path]:
    if not backups_dir.is_dir():
        return []
    return sorted(backups_dir.glob("vascqbank-*.sqlite3"))


def prune_automatic_backups(backups_dir: Path, keep: int = KEEP_AUTOMATIC) -> list[Path]:
    """Delete the oldest automatic backups beyond *keep*; manual ones are never touched."""
    automatic = [
        p for p in list_backups(backups_dir) if any(f"-{r}" in p.stem for r in AUTOMATIC_REASONS)
    ]
    removed = automatic[: max(0, len(automatic) - keep)]
    for path in removed:
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".json").unlink(missing_ok=True)
    return removed
