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
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop import __version__
from vascqbank_desktop.config.constants import SQLITE_APPLICATION_ID
from vascqbank_desktop.db.errors import DatabaseError

AUTOMATIC_REASONS = ("pre-import", "pre-migration")
KEEP_AUTOMATIC = 20
# Phase 9: the daily start-up backup has its own, smaller retention so that it can never push
# pre-import or pre-migration backups out (and vice versa).
DAILY_REASON = "automatic"
KEEP_DAILY = 10
MAX_MANIFEST_BYTES = 1 << 20  # a real backup manifest is < 2 KB
_BACKUP_NAME = re.compile(r"^vascqbank-\d{8}-\d{6}-\d{6}-(?P<reason>[a-z0-9-]+)\.sqlite3$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


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


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":  # directories cannot be opened for fsync on Windows; NTFS journals renames
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json_durably(path: Path, data: dict[str, object]) -> None:
    """Temp file in the same folder → fsync → atomic rename: a reader sees all of it or nothing."""
    temp = path.with_name(path.name + ".tmp")
    try:
        with temp.open("w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    except BaseException:
        temp.unlink(missing_ok=True)
        raise


def change_signature(conn: sqlite3.Connection) -> str:
    """A cheap fingerprint of everything a reviewer can change (Phase 9 automatic-backup policy).

    Revisions, review events, import batches, export runs and discarded drafts are append-only, so
    their counts grow with every change; ``lock_version`` grows with every decision and edit; a
    draft write raises ``write_count`` or ``saved_at``. Equal signatures therefore mean "nothing a
    reviewer did since then" — a backup of an unchanged database is never repeated.
    """
    counts = _table_counts(conn)
    parts: dict[str, object] = {"counts": counts}
    if "questions" in counts:
        row = conn.execute(
            "SELECT coalesce(sum(lock_version), 0), coalesce(max(modified_at), '') FROM questions"
        ).fetchone()
        parts["questions"] = [row[0], row[1]]
    if "drafts" in counts:
        row = conn.execute(
            "SELECT coalesce(sum(write_count), 0), coalesce(max(saved_at), ''), "
            "coalesce(sum(length(stem) + length(explanation)), 0) FROM drafts"
        ).fetchone()
        parts["drafts"] = [row[0], row[1], row[2]]
    if "app_metadata" in counts:
        parts["metadata"] = [
            list(r) for r in conn.execute("SELECT key, value FROM app_metadata ORDER BY key")
        ]
    blob = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def create_backup(conn: sqlite3.Connection, backups_dir: Path, reason: str) -> BackupInfo:
    """Back up the database behind *conn* into *backups_dir* and verify it.

    Order (Phase 9): copy → verify → hash → fsync → **manifest written durably** → atomic rename →
    folder fsync. A backup file therefore never becomes visible without its complete manifest; an
    interruption leaves at most an unlisted ``.partial`` / orphan manifest, never a half backup.
    """
    backups_dir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC)
    stamp = now.strftime("%Y%m%d-%H%M%S-%f")
    reason_slug = _slug(reason)
    final = backups_dir / f"vascqbank-{stamp}-{reason_slug}.sqlite3"
    partial = final.with_name(final.name + ".partial")
    manifest_path = final.with_name(final.name + ".json")

    # One read snapshot for the counts, the signature and the copy (Phase 9): a commit from another
    # connection meanwhile (autosave during the start-up backup) cannot make them disagree. The
    # backup API reuses the source connection's open read transaction (verified experimentally).
    own_snapshot = not conn.in_transaction
    try:
        if own_snapshot:
            conn.execute("BEGIN")
        expected_counts = _table_counts(conn)
        signature = change_signature(conn)
        target = sqlite3.connect(partial)
        try:
            conn.backup(target)
            target.execute("PRAGMA journal_mode = DELETE")
        finally:
            target.close()
            if own_snapshot and conn.in_transaction:
                conn.execute("COMMIT")
        version, database_id, counts = verify_backup(partial)
        if counts != expected_counts:
            raise BackupError(f"Backup row counts differ: {counts} != {expected_counts}")
        digest = hashlib.sha256()
        size = 0
        with partial.open("rb+") as handle:
            for block in iter(lambda: handle.read(1 << 20), b""):
                digest.update(block)
                size += len(block)
            os.fsync(handle.fileno())
        info = BackupInfo(
            path=final,
            manifest_path=manifest_path,
            reason=reason_slug,
            created_at=now.replace(microsecond=0).isoformat(),
            sha256=digest.hexdigest(),
            size=size,
            schema_version=version,
            database_id=database_id,
            table_counts=counts,
        )
        manifest = {k: str(v) if isinstance(v, Path) else v for k, v in asdict(info).items()}
        manifest["path"] = final.name
        manifest["manifest_path"] = manifest_path.name
        manifest["app_version"] = __version__
        manifest["change_signature"] = signature
        _write_json_durably(manifest_path, manifest)
        partial.replace(final)
        _fsync_dir(backups_dir)
    except BaseException:
        if own_snapshot and conn.in_transaction:
            conn.execute("ROLLBACK")
        partial.unlink(missing_ok=True)
        if not final.exists():
            manifest_path.unlink(missing_ok=True)
        raise
    return info


class BackupProblem(StrEnum):
    NONE = "NONE"
    MISSING_FILE = "MISSING_FILE"
    SIZE_MISMATCH = "SIZE_MISMATCH"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    UNREADABLE = "UNREADABLE"  # not a valid database, or integrity_check failed
    COUNTS_MISMATCH = "COUNTS_MISMATCH"
    MANIFEST_INVALID = "MANIFEST_INVALID"  # the manifest lacks a valid size / SHA-256 (Phase 9)


@dataclass(frozen=True, slots=True)
class BackupCheck:
    """Result of re-verifying an existing backup file (read-only)."""

    ok: bool
    problem: BackupProblem
    has_manifest: bool
    schema_version: int | None = None
    table_counts: dict[str, int] | None = None
    message: str = ""


def read_manifest(backup_path: Path) -> dict[str, object] | None:
    """The JSON manifest beside a backup, or ``None`` if absent, unreadable or implausible.

    Phase 9: never raises for a damaged file (deep nesting, huge size, invalid UTF-8).
    """
    manifest_path = backup_path.with_name(backup_path.name + ".json")
    try:
        if manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
            return None
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def manifest_problems(manifest: dict[str, object]) -> list[str]:
    """Fields a manifest must have for its backup to count as verifiable (Phase 9)."""
    problems: list[str] = []
    size = manifest.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        problems.append("size is missing or not a number")
    digest = manifest.get("sha256")
    if not isinstance(digest, str) or not _SHA256.match(digest):
        problems.append("sha256 is missing or malformed")
    counts = manifest.get("table_counts")
    if counts is not None and not (
        isinstance(counts, dict)
        and all(
            isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) for k, v in counts.items()
        )
    ):
        problems.append("table_counts is malformed")
    return problems


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _failure(problem: BackupProblem, message: str, *, has_manifest: bool) -> BackupCheck:
    return BackupCheck(False, problem, has_manifest, message=message)


def check_backup_file(path: Path) -> BackupCheck:
    """Verify an existing backup: size and SHA-256 against its manifest (when there is
    one), then open it read-only and run ``integrity_check`` and compare row counts.

    Never modifies the backup. Used by the backup list; creating a backup already
    performs the same verification before the file is accepted.
    """
    if not path.is_file():
        return _failure(BackupProblem.MISSING_FILE, f"{path.name} does not exist", has_manifest=False)
    manifest = read_manifest(path)
    has_manifest = manifest is not None
    try:
        if manifest is not None:
            malformed = manifest_problems(manifest)
            if malformed:  # a manifest that cannot vouch for the file is not silently skipped
                return _failure(BackupProblem.MANIFEST_INVALID, "; ".join(malformed), has_manifest=True)
            expected_size = manifest.get("size")
            if isinstance(expected_size, int) and path.stat().st_size != expected_size:
                return _failure(
                    BackupProblem.SIZE_MISMATCH, "file size differs from the manifest", has_manifest=True
                )
            expected_hash = manifest.get("sha256")
            if isinstance(expected_hash, str) and _sha256_file(path) != expected_hash:
                return _failure(
                    BackupProblem.CHECKSUM_MISMATCH, "SHA-256 differs from the manifest", has_manifest=True
                )
        version, _database_id, counts = verify_backup(path)
    except (BackupError, OSError) as exc:
        return _failure(BackupProblem.UNREADABLE, str(exc), has_manifest=has_manifest)
    if manifest is not None and manifest.get("table_counts") not in (None, counts):
        return BackupCheck(
            False, BackupProblem.COUNTS_MISMATCH, True, version, counts, "row counts differ from the manifest"
        )
    return BackupCheck(True, BackupProblem.NONE, has_manifest, version, counts)


def list_backups(backups_dir: Path) -> list[Path]:
    if not backups_dir.is_dir():
        return []
    return sorted(p for p in backups_dir.glob("vascqbank-*.sqlite3") if not p.is_symlink())


_LEFTOVER = re.compile(
    r"^(vascqbank-\d{8}-\d{6}-\d{6}-[a-z0-9-]+\.sqlite3\.(partial|json\.tmp)"
    r"|\.vascqbank-\d{8}-\d{6}-\d{6}-[a-z0-9-]+\.sqlite3(\.json)?\.[0-9a-f]{32}\.partial)$"
)
STALE_AFTER_S = 3600


def remove_stale_leftovers(backups_dir: Path, *, now: float | None = None) -> list[Path]:
    """Delete half-written files of interrupted backups / imports (Phase 9).

    Only names this program writes while a backup is *being made* (``….sqlite3.partial``,
    ``….json.tmp``, hidden ``.….partial`` copies) and only when older than an hour, so a backup
    that is being written right now is never touched. Finished backups and manifests never match.
    """
    if not backups_dir.is_dir():
        return []
    moment = time.time() if now is None else now
    removed: list[Path] = []
    for path in backups_dir.iterdir():
        try:
            if (
                _LEFTOVER.match(path.name)
                and path.is_file()
                and not path.is_symlink()
                and moment - path.stat().st_mtime > STALE_AFTER_S
            ):
                path.unlink()
                removed.append(path)
        except OSError:
            continue
    return removed


def backup_reason(path: Path) -> str | None:
    """The reason slug of a backup file name written by :func:`create_backup` (else ``None``)."""
    match = _BACKUP_NAME.match(path.name)
    return match["reason"] if match else None


def _is_pruneable(path: Path, reasons: tuple[str, ...]) -> bool:
    reason = backup_reason(path)
    if reason is None:
        return False
    return any(
        reason == r or (r == "pre-migration" and re.fullmatch(r"pre-migration-v\d+", reason)) for r in reasons
    )


def prune_automatic_backups(
    backups_dir: Path, keep: int = KEEP_AUTOMATIC, *, reasons: tuple[str, ...] = AUTOMATIC_REASONS
) -> list[Path]:
    """Delete the oldest automatic backups beyond *keep*; manual ones are never touched.

    Phase 9: the reason must match the file name exactly (``…-pre-import.sqlite3``,
    ``…-pre-migration-v3.sqlite3``); before, any name merely *containing* ``-pre-import`` counted,
    so e.g. a copied-in file could be deleted. Manual, pre-restore and imported backups never match.
    """
    automatic = [p for p in list_backups(backups_dir) if _is_pruneable(p, reasons)]
    removed = automatic[: max(0, len(automatic) - keep)]
    for path in removed:
        path.unlink(missing_ok=True)
        path.with_name(path.name + ".json").unlink(missing_ok=True)
    return removed
