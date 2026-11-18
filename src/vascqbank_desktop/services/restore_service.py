"""Restoring the working database from a verified backup (Phase 8).

Two situations are supported, and both need an explicit decision by the reviewer:

* **A — the working database is missing** (the start-up recovery message);
* **B — the working database exists** and the reviewer asks to go back to an older backup.

The protocol (ARCHITECTURE.md §28) never copies a live SQLite file byte-wise and never deletes
the current database:

1. :func:`inspect_backup` — the backup must have its manifest; size and SHA-256 must match it; the
   file is opened read-only (``immutable``) and must carry this application's id, a supported schema
   version, a clean ``integrity_check`` and ``foreign_key_check`` and the row counts of the manifest.
   The reviewer sees its date and question/review counts and confirms; the confirmed SHA-256 is
   passed back, so a file changed after the check is refused.
2. (B) The live database is checkpointed (``wal_checkpoint(TRUNCATE)``) — if another connection is
   using it, the restore is refused — and a separate **safety backup** (``pre-restore``) is created
   and verified with the ordinary backup code. Safety backups are never pruned.
3. The backup is copied with SQLite's backup API into ``database/.restore-<id>.partial``; restore
   provenance is recorded in ``app_metadata``; the copy is switched to a single file, verified the
   same way as the backup (plus equal row counts) and ``fsync``-ed.
4. A durable journal ``journal/restore.json`` records the intent (backup, hashes, file names).
5. (B) The live file is **hard-linked** into ``displaced/`` (copied and verified where links are not
   possible) and its stale side files are moved there too; then the copy replaces the live file
   with one atomic ``os.replace``. (A) Stale ``-wal``/``-shm``/``-journal`` files of the lost
   database are moved to ``displaced/`` — a stale WAL would otherwise be replayed into the restored
   database — and the copy is published with a no-overwrite link.
6. The journal is marked ``published``; the derived search index is deleted (rebuilt later); the
   database is reopened through :func:`initialize_storage` (migrating an older schema behind its own
   pre-migration backup) and only then is the restore reported as successful.

An ordinary error rolls back in-process (:func:`_recover`). After a crash
:func:`recover_interrupted_restore` runs at start-up, before anything opens the database: before
publication it rolls back (the newer live database stays — an older one is never chosen silently);
after publication it completes; anything it cannot prove (e.g. a live file matching neither recorded
hash) is :attr:`RecoveryOutcome.AMBIGUOUS` — nothing is touched and the reviewer decides.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop import __version__
from vascqbank_desktop.config.constants import SQLITE_APPLICATION_ID
from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.db.migrations import LATEST_VERSION
from vascqbank_desktop.db.schema_check import schema_differences
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services.seed_service import sha256_file
from vascqbank_desktop.services.storage import StorageStatus, initialize_storage

log = logging.getLogger(__name__)

JOURNAL_FORMAT = 1
SAFETY_REASON = "pre-restore"
TEMP_PREFIX = ".restore-"
TEMP_SUFFIX = ".partial"
SIDE_SUFFIXES = ("-wal", "-shm", "-journal")
META_RESTORED_FROM = "restored_from_backup"
META_RESTORED_SHA = "restored_from_sha256"
META_RESTORED_AT = "restored_at"


def _checkpoint(stage: str) -> None:
    """Named points of a restore; tests replace this to interrupt it exactly there."""


# ----------------------------------------------------------------------------------- errors
class RestoreProblem(StrEnum):
    NO_MANIFEST = "NO_MANIFEST"
    MISSING_FILE = "MISSING_FILE"
    SIZE_MISMATCH = "SIZE_MISMATCH"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    UNREADABLE = "UNREADABLE"
    FOREIGN_APPLICATION = "FOREIGN_APPLICATION"
    INTEGRITY = "INTEGRITY"
    FOREIGN_KEYS = "FOREIGN_KEYS"
    SCHEMA_UNSUPPORTED = "SCHEMA_UNSUPPORTED"
    COUNTS_MISMATCH = "COUNTS_MISMATCH"
    SCHEMA_TAMPERED = "SCHEMA_TAMPERED"  # Phase 9b: objects our migrations never create (or missing)
    CHANGED_SINCE_CHECK = "CHANGED_SINCE_CHECK"
    DATABASE_BUSY = "DATABASE_BUSY"
    SAFETY_BACKUP_FAILED = "SAFETY_BACKUP_FAILED"
    COPY_FAILED = "COPY_FAILED"
    PUBLISH_FAILED = "PUBLISH_FAILED"
    REOPEN_FAILED = "REOPEN_FAILED"
    INTERRUPTED_RESTORE_PENDING = "INTERRUPTED_RESTORE_PENDING"


class RestoreRefused(Exception):
    """The restore did not happen; the working database (if any) is exactly as before."""

    def __init__(self, problem: RestoreProblem, detail: str = "") -> None:
        super().__init__(f"{problem.value}: {detail}" if detail else problem.value)
        self.problem = problem
        self.detail = detail


# ----------------------------------------------------------------------------------- inspection
@dataclass(frozen=True, slots=True)
class BackupSummary:
    """What the reviewer is shown before confirming (all read from the verified file)."""

    path: Path
    sha256: str
    size: int
    created_at: str | None
    reason: str
    app_version: str | None
    schema_version: int
    database_id: str
    questions: int
    by_status: dict[ReviewStatus, int]
    revisions: int
    drafts: int
    review_events: int
    table_counts: dict[str, int]

    def count(self, status: ReviewStatus) -> int:
        return self.by_status.get(status, 0)


def _read_only(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    return sqlite3.connect(uri, uri=True, isolation_level=None)


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return (
        conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?", (name,)).fetchone()
        is not None
    )


def _database_facts(conn: sqlite3.Connection) -> tuple[int, str, dict[str, int]]:
    """Check a database file (read-only connection); return (schema, database id, row counts)."""
    app_id = int(conn.execute("PRAGMA application_id").fetchone()[0])
    if app_id != SQLITE_APPLICATION_ID:
        raise RestoreRefused(RestoreProblem.FOREIGN_APPLICATION, f"application id {app_id:#010x}")
    problems = [str(r[0]) for r in conn.execute("PRAGMA integrity_check")]
    if problems != ["ok"]:
        raise RestoreRefused(RestoreProblem.INTEGRITY, "; ".join(problems[:5]))
    violations = conn.execute("PRAGMA foreign_key_check").fetchall()
    if violations:
        first = violations[0]
        raise RestoreRefused(
            RestoreProblem.FOREIGN_KEYS,
            f"{len(violations)} violation(s), first in {first[0]} rowid {first[1]}",
        )
    version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    if not 1 <= version <= LATEST_VERSION:
        raise RestoreRefused(
            RestoreProblem.SCHEMA_UNSUPPORTED, f"schema version {version}, supported 1..{LATEST_VERSION}"
        )
    # Phase 9b: a consistent file can still carry a trigger, view or index we never created (a trigger
    # would run inside the program's own transactions after the restore) or lack a CHECK / FK.
    tampered = schema_differences(conn, version)
    if tampered:
        raise RestoreRefused(RestoreProblem.SCHEMA_TAMPERED, "; ".join(tampered))
    database_id = ""
    if _table_exists(conn, "app_metadata"):
        row = conn.execute("SELECT value FROM app_metadata WHERE key = 'database_id'").fetchone()
        database_id = str(row[0]) if row else ""
    return version, database_id, backup_db._table_counts(conn)


def inspect_backup(path: Path) -> BackupSummary:
    """Verify a backup for restoring (read-only) and describe it; raises :class:`RestoreRefused`."""
    if not path.is_file():
        raise RestoreRefused(RestoreProblem.MISSING_FILE, path.name)
    manifest = backup_db.read_manifest(path)
    if manifest is None:
        raise RestoreRefused(RestoreProblem.NO_MANIFEST, f"{path.name}.json")
    size = path.stat().st_size
    if manifest.get("size") != size:
        raise RestoreRefused(RestoreProblem.SIZE_MISMATCH, f"{size} != {manifest.get('size')}")
    digest = sha256_file(path)
    if manifest.get("sha256") != digest:
        raise RestoreRefused(RestoreProblem.CHECKSUM_MISMATCH, path.name)
    try:
        conn = _read_only(path)
    except sqlite3.Error as exc:
        raise RestoreRefused(RestoreProblem.UNREADABLE, str(exc)) from exc
    try:
        version, database_id, counts = _database_facts(conn)
        by_status: dict[ReviewStatus, int] = {}
        if _table_exists(conn, "questions"):
            for status, number in conn.execute("SELECT status, count(*) FROM questions GROUP BY status"):
                try:
                    by_status[ReviewStatus(status)] = int(number)
                except ValueError:
                    continue
    except sqlite3.Error as exc:
        raise RestoreRefused(RestoreProblem.UNREADABLE, str(exc)) from exc
    finally:
        conn.close()
    expected_counts = manifest.get("table_counts")
    if isinstance(expected_counts, dict) and expected_counts != counts:
        raise RestoreRefused(RestoreProblem.COUNTS_MISMATCH, "row counts differ from the manifest")
    created = manifest.get("created_at")
    app_version = manifest.get("app_version")
    return BackupSummary(
        path=path,
        sha256=digest,
        size=size,
        created_at=created if isinstance(created, str) else None,
        reason=str(manifest.get("reason") or ""),
        app_version=app_version if isinstance(app_version, str) else None,
        schema_version=version,
        database_id=database_id,
        questions=counts.get("questions", 0),
        by_status=by_status,
        revisions=counts.get("question_revisions", 0),
        drafts=counts.get("drafts", 0),
        review_events=counts.get("review_events", 0),
        table_counts=counts,
    )


# ----------------------------------------------------------------------------------- file helpers
def journal_path(paths: AppPaths) -> Path:
    return paths.data_dir / "journal" / "restore.json"


def displaced_dir(paths: AppPaths) -> Path:
    """Where replaced databases (and stale side files) are kept — never deleted by the program."""
    return paths.data_dir / "displaced"


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_file(path: Path) -> None:
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def _write_journal(paths: AppPaths, data: dict[str, object]) -> None:
    target = journal_path(paths)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.with_name(target.name + ".tmp")
    with temp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, target)
    _fsync_dir(target.parent)


MAX_JOURNAL_BYTES = 1 << 20  # a real journal is < 2 KB; anything huge is not ours
_STAMP = re.compile(r"^\d{8}-\d{6}-\d{6}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TEMP_NAME = re.compile(r"^\.restore-[0-9a-f]{32}\.partial$")
_STAGES = ("prepared", "displaced", "published")


def _plain_name(value: object) -> bool:
    """A single file name in one of our folders: no separator, no parent reference, no drive."""
    return (
        isinstance(value, str)
        and 0 < len(value) <= 255
        and value not in (".", "..")
        and not any(c in value for c in "/\\:\x00")
        and value.isprintable()
    )


def journal_problems(data: object) -> list[str]:
    """Why a parsed restore journal cannot be trusted (empty list = every field is what we write).

    Phase 9: every field that becomes a path is checked against the exact pattern this module
    writes, so a damaged or crafted journal can never make the recovery delete or move a file
    outside ``database/`` and ``displaced/`` (e.g. ``"temp": "/home/user/thesis.odt"``).
    """
    if not isinstance(data, dict):
        return ["not a JSON object"]
    problems: list[str] = []
    if data.get("format") != JOURNAL_FORMAT:
        problems.append("unknown journal format")
    if data.get("stage") not in _STAGES:
        problems.append("unknown stage")
    if not isinstance(data.get("live_existed"), bool):
        problems.append("live_existed is not a boolean")
    if not isinstance(data.get("temp"), str) or not _TEMP_NAME.match(data["temp"]):
        problems.append("temp is not a restore copy name")
    stamp = data.get("stamp")
    if not isinstance(stamp, str) or not _STAMP.match(stamp):
        problems.append("stamp is malformed")
    for key in ("temp_sha256", "backup_sha256"):
        if not isinstance(data.get(key), str) or not _SHA256.match(data[key]):
            problems.append(f"{key} is not a SHA-256")
    original = data.get("original_sha256")
    if data.get("live_existed") is True and (not isinstance(original, str) or not _SHA256.match(original)):
        problems.append("original_sha256 is not a SHA-256")
    if not _plain_name(data.get("backup")):
        problems.append("backup is not a plain file name")
    safety = data.get("safety_backup")
    if safety is not None and not _plain_name(safety):
        problems.append("safety_backup is not a plain file name")
    displaced = data.get("displaced")
    if not isinstance(displaced, list) or len(displaced) > 8:
        problems.append("displaced is not a short list")
    elif isinstance(stamp, str):
        prefix = f"vascqbank-{stamp}-displaced.sqlite3"
        for name in displaced:
            if not (_plain_name(name) and name in (prefix, *(prefix + s for s in SIDE_SUFFIXES))):
                problems.append("displaced names a file this restore did not create")
                break
    return problems


def _read_journal(paths: AppPaths) -> dict[str, object] | None:
    path = journal_path(paths)
    try:
        if path.stat().st_size > MAX_JOURNAL_BYTES:
            log.error("Restore journal %s is implausibly large; not trusted", path)
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    problems = journal_problems(data)
    if problems:
        log.error("Restore journal %s is not trusted: %s", path, "; ".join(problems))
        return None
    return data


def _remove_journal(paths: AppPaths) -> None:
    journal_path(paths).unlink(missing_ok=True)
    journal_path(paths).with_name("restore.json.tmp").unlink(missing_ok=True)
    if journal_path(paths).parent.is_dir():  # Phase 9b: the folder may never have been created
        _fsync_dir(journal_path(paths).parent)


def _side_files(database: Path) -> list[Path]:
    return [p for p in (Path(f"{database}{s}") for s in SIDE_SUFFIXES) if p.exists()]


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")


def _remove_search_index(paths: AppPaths) -> None:
    for candidate in (
        paths.search_index_file,
        *(Path(f"{paths.search_index_file}{s}") for s in SIDE_SUFFIXES),
    ):
        candidate.unlink(missing_ok=True)


def stray_temporary_files(paths: AppPaths) -> list[Path]:
    if not paths.database_dir.is_dir():
        return []
    return sorted(
        p
        for p in paths.database_dir.iterdir()
        if p.name.startswith(TEMP_PREFIX) and TEMP_SUFFIX in p.name and p.is_file()
    )


# ----------------------------------------------------------------------------------- restore
@dataclass(frozen=True, slots=True)
class RestoreResult:
    summary: BackupSummary
    safety_backup: Path | None
    displaced: tuple[Path, ...]
    status: StorageStatus
    timings: dict[str, float] = field(default_factory=dict)


def _copy_into_temp(paths: AppPaths, summary: BackupSummary, temp: Path) -> str:
    """Copy the backup into *temp* with the backup API, record provenance, verify; return its hash."""
    source = _read_only(summary.path)
    try:
        target = sqlite3.connect(temp, isolation_level=None)
        try:
            source.backup(target)
            if _table_exists(target, "app_metadata"):
                target.execute("BEGIN IMMEDIATE")
                target.executemany(
                    "INSERT OR REPLACE INTO app_metadata (key, value) VALUES (?, ?)",
                    [
                        (META_RESTORED_FROM, summary.path.name),
                        (META_RESTORED_SHA, summary.sha256),
                        (META_RESTORED_AT, datetime.now(UTC).replace(microsecond=0).isoformat()),
                    ],
                )
                target.execute("COMMIT")
            target.execute("PRAGMA journal_mode = DELETE")  # one self-contained file, no WAL
        finally:
            target.close()
    finally:
        source.close()
    _fsync_file(temp)
    check = _read_only(temp)
    try:
        version, database_id, counts = _database_facts(check)
    finally:
        check.close()
    expected = {k: v for k, v in summary.table_counts.items() if k != "app_metadata"}
    if {
        k: v for k, v in counts.items() if k != "app_metadata"
    } != expected or database_id != summary.database_id:
        raise RestoreRefused(RestoreProblem.COPY_FAILED, "the copy differs from the backup")
    if version != summary.schema_version:
        raise RestoreRefused(RestoreProblem.COPY_FAILED, "schema version changed while copying")
    return sha256_file(temp)


def _quiesce_live_database(paths: AppPaths) -> None:
    """Fold the WAL into the main file; refuse if another connection is using the database."""
    conn = open_database(paths.database_file)
    try:
        busy, _log_frames, _done = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        conn.close()
    # SQLite removes -wal/-shm when the LAST connection closes: if they are still there, another
    # connection (another window or program) has the database open and might write to it.
    if busy or any(Path(f"{paths.database_file}{s}").exists() for s in ("-wal", "-shm")):
        raise RestoreRefused(RestoreProblem.DATABASE_BUSY, "the database is in use by another connection")


def _displace(paths: AppPaths, *, include_database: bool, stamp: str) -> list[Path]:
    """Keep the live file (hard link, or verified copy) and move stale side files to ``displaced/``."""
    folder = displaced_dir(paths)
    folder.mkdir(parents=True, exist_ok=True)
    kept: list[Path] = []
    live = paths.database_file
    if include_database:
        target = folder / f"vascqbank-{stamp}-displaced.sqlite3"
        try:
            os.link(live, target)
        except (OSError, NotImplementedError, AttributeError):
            shutil.copy2(live, target)
            _fsync_file(target)
            if sha256_file(target) != sha256_file(live):
                target.unlink(missing_ok=True)
                raise RestoreRefused(
                    RestoreProblem.PUBLISH_FAILED, "the current database could not be kept"
                ) from None
        kept.append(target)
    for side in _side_files(live):
        target = folder / f"vascqbank-{stamp}-displaced.sqlite3{side.name[len(live.name) :]}"
        os.replace(side, target)
        kept.append(target)
    _fsync_dir(folder)
    _fsync_dir(paths.database_dir)
    return kept


def restore_backup(
    paths: AppPaths,
    backup_path: Path,
    *,
    expected_sha256: str,
    progress: Callable[[str], None] | None = None,
) -> RestoreResult:
    """Replace (B) or recreate (A) the working database from a verified backup; see the module
    docstring. Raises :class:`RestoreRefused` — then the previous data is untouched."""

    def step(name: str) -> None:
        if progress is not None:
            progress(name)

    timings: dict[str, float] = {}
    started = time.perf_counter()
    try:
        paths.ensure_directories()
        if journal_path(paths).exists():
            raise RestoreRefused(RestoreProblem.INTERRUPTED_RESTORE_PENDING, str(journal_path(paths)))
        for stray in stray_temporary_files(paths):  # left by a restore that crashed before its journal
            stray.unlink()

        step("verify")
        summary = inspect_backup(backup_path)
    except (OSError, sqlite3.Error) as exc:  # Phase 9b: never an unclassified error (nothing touched yet)
        raise RestoreRefused(RestoreProblem.UNREADABLE, f"{type(exc).__name__}: {exc}") from exc
    if summary.sha256 != expected_sha256:
        raise RestoreRefused(RestoreProblem.CHANGED_SINCE_CHECK, backup_path.name)
    timings["verify"] = time.perf_counter() - started
    _checkpoint("verified")

    live = paths.database_file
    live_existed = live.exists()
    safety: Path | None = None
    original_sha: str | None = None
    if live_existed:
        step("safety-backup")
        mark = time.perf_counter()
        try:
            _quiesce_live_database(paths)
            conn = open_database(live)
            try:
                safety = backup_db.create_backup(conn, paths.backups_dir, SAFETY_REASON).path
            finally:
                conn.close()
            _quiesce_live_database(paths)
            original_sha = sha256_file(live)
        except RestoreRefused:
            raise
        except Exception as exc:  # Phase 9b: opening the live database counts too, not only the copy
            problem = (
                RestoreProblem.DATABASE_BUSY
                if isinstance(exc, sqlite3.OperationalError) and "locked" in str(exc).lower()
                else RestoreProblem.SAFETY_BACKUP_FAILED
            )
            raise RestoreRefused(problem, f"{type(exc).__name__}: {exc}") from exc
        timings["safety_backup"] = time.perf_counter() - mark
        _checkpoint("safety-backup")

    step("copy")
    mark = time.perf_counter()
    temp = paths.database_dir / f"{TEMP_PREFIX}{uuid.uuid4().hex}{TEMP_SUFFIX}"
    try:
        temp_sha = _copy_into_temp(paths, summary, temp)
    except RestoreRefused:
        temp.unlink(missing_ok=True)
        raise
    except (sqlite3.Error, OSError) as exc:
        temp.unlink(missing_ok=True)
        raise RestoreRefused(RestoreProblem.COPY_FAILED, str(exc)) from exc
    timings["copy"] = time.perf_counter() - mark
    try:
        _checkpoint("temp-created")
    except BaseException:
        temp.unlink(missing_ok=True)
        raise

    stamp = _stamp()
    journal: dict[str, object] = {
        "format": JOURNAL_FORMAT,
        "stage": "prepared",
        "started_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "app_version": __version__,
        "backup": summary.path.name,
        "backup_sha256": summary.sha256,
        "live_existed": live_existed,
        "original_sha256": original_sha,
        "safety_backup": safety.name if safety else None,
        "temp": temp.name,
        "temp_sha256": temp_sha,
        "stamp": stamp,
        "displaced": [],
    }
    step("publish")
    mark = time.perf_counter()
    try:
        _write_journal(paths, journal)
        _checkpoint("journal-written")
        if live_existed and (not live.exists() or sha256_file(live) != original_sha):
            raise RestoreRefused(RestoreProblem.DATABASE_BUSY, "the database changed during the restore")
        kept = _displace(paths, include_database=live_existed, stamp=stamp)
        journal["displaced"] = [p.name for p in kept]
        journal["stage"] = "displaced"
        _write_journal(paths, journal)
        _checkpoint("displaced")
        if live_existed:
            os.replace(temp, live)  # atomic: the old file stays reachable in displaced/
        else:
            try:
                _publish_no_overwrite(temp, live)
            except FileExistsError as exc:
                raise RestoreRefused(RestoreProblem.PUBLISH_FAILED, "a database appeared meanwhile") from exc
        _fsync_dir(paths.database_dir)
        _checkpoint("replaced")
        journal["stage"] = "published"
        _write_journal(paths, journal)
    except BaseException as exc:
        outcome = _recover(paths, journal)
        log.error("Restore failed before completion (%s); recovery: %s", exc, outcome.outcome)
        if isinstance(exc, RestoreRefused):
            raise
        if isinstance(exc, Exception):
            raise RestoreRefused(RestoreProblem.PUBLISH_FAILED, str(exc)) from exc
        raise
    timings["publish"] = time.perf_counter() - mark
    _checkpoint("published")  # from here on the restore is complete; only re-opening remains

    step("reopen")
    mark = time.perf_counter()
    try:
        status = _finish(paths)
    except Exception as exc:
        # The restored file is published and verified, but cannot be opened by the application: put
        # the previous database back (B) — the restored copy is kept in displaced/ for inspection.
        log.exception("The restored database could not be reopened")
        _undo_publication(paths, journal)
        raise RestoreRefused(RestoreProblem.REOPEN_FAILED, str(exc)) from exc
    timings["reopen"] = time.perf_counter() - mark
    timings["total"] = time.perf_counter() - started
    log.info(
        "Restored %s (%s questions); safety backup %s; displaced %s",
        summary.path.name,
        summary.questions,
        safety.name if safety else "-",
        ", ".join(str(p) for p in kept) or "-",
    )
    return RestoreResult(
        summary=summary,
        safety_backup=safety,
        displaced=tuple(displaced_dir(paths) / name for name in journal["displaced"]),  # type: ignore[union-attr]
        status=status,
        timings=timings,
    )


def _publish_no_overwrite(temp: Path, final: Path) -> None:
    try:
        os.link(temp, final)
    except FileExistsError:
        raise
    except (OSError, NotImplementedError, AttributeError):
        if final.exists():
            raise FileExistsError(str(final)) from None
        os.rename(temp, final)
        return
    temp.unlink()


def _finish(paths: AppPaths) -> StorageStatus:
    """After publication: drop the derived index, reopen and verify, forget the journal."""
    _remove_search_index(paths)
    _checkpoint("index-removed")
    status = initialize_storage(paths)
    if not status.integrity_ok:
        raise RestoreRefused(RestoreProblem.REOPEN_FAILED, "quick_check failed after reopening")
    _remove_journal(paths)
    _checkpoint("completed")
    return status


def _undo_publication(paths: AppPaths, journal: dict[str, object]) -> None:
    """Reopening the restored database failed: put the displaced database back, keep the restored one."""
    names = journal.get("displaced") or []
    database = next((n for n in names if str(n).endswith("-displaced.sqlite3")), None)  # type: ignore[union-attr]
    folder = displaced_dir(paths)
    live = paths.database_file
    keep = folder / f"vascqbank-{journal.get('stamp')}-restored-unusable.sqlite3"
    if live.exists():
        os.replace(live, keep)
        for side in _side_files(live):
            os.replace(side, Path(f"{keep}{side.name[len(live.name) :]}"))
    if database is not None and (folder / str(database)).exists():
        partial = paths.database_dir / f"{TEMP_PREFIX}undo{TEMP_SUFFIX}"
        shutil.copy2(folder / str(database), partial)
        _fsync_file(partial)
        os.replace(partial, live)
    _fsync_dir(paths.database_dir)
    _remove_journal(paths)


def set_aside_damaged_database(paths: AppPaths) -> tuple[Path, ...]:
    """Move a damaged working database (and its side files) into ``displaced/`` — never delete it.

    Phase 9b. Used only after the reviewer explicitly chose to restore a backup in place of a working
    database that start-up found damaged (``Outcome.DATABASE_DAMAGED``). Each file is *renamed*
    (atomic, same folder tree, no copy that could fail half-way) to
    ``displaced/vascqbank-<stamp>-damaged.sqlite3[-wal|-shm|-journal]`` and so stays available
    byte-for-byte; afterwards the folder is in the ordinary "database missing" state, whose restore
    (situation A) is journaled and verified. Refused while an interrupted restore is pending.
    """
    if journal_path(paths).exists():
        raise RestoreRefused(RestoreProblem.INTERRUPTED_RESTORE_PENDING, str(journal_path(paths)))
    live = paths.database_file
    if not live.is_file():
        return ()
    folder = displaced_dir(paths)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"vascqbank-{_stamp()}-damaged.sqlite3"
    moved: list[Path] = []
    for source in (live, *_side_files(live)):
        destination = Path(f"{target}{source.name[len(live.name) :]}")
        if destination.exists():  # never overwrite anything (the stamp makes this practically impossible)
            raise RestoreRefused(RestoreProblem.PUBLISH_FAILED, f"{destination.name} already exists")
        os.replace(source, destination)
        moved.append(destination)
    _fsync_dir(folder)
    _fsync_dir(paths.database_dir)
    log.warning("Damaged working database set aside: %s", ", ".join(p.name for p in moved))
    return tuple(moved)


# ----------------------------------------------------------------------------------- recovery
class RecoveryOutcome(StrEnum):
    NONE = "NONE"  # no interrupted restore
    ROLLED_BACK = "ROLLED_BACK"  # interrupted before publication: everything as before the restore
    COMPLETED = "COMPLETED"  # interrupted after publication: the restore was finished
    AMBIGUOUS = "AMBIGUOUS"  # cannot be proven either way: nothing touched, the reviewer decides


@dataclass(frozen=True, slots=True)
class RestoreRecovery:
    outcome: RecoveryOutcome
    detail: str = ""
    backup: str | None = None
    candidates: tuple[Path, ...] = ()
    removed: tuple[Path, ...] = ()


def _candidates(paths: AppPaths, journal: dict[str, object] | None) -> tuple[Path, ...]:
    found: list[Path] = []
    if paths.database_file.exists():
        found.append(paths.database_file)
    found += stray_temporary_files(paths)
    folder = displaced_dir(paths)
    if folder.is_dir():
        found += sorted(p for p in folder.iterdir() if p.is_file())
    if journal and journal.get("safety_backup"):
        safety = paths.backups_dir / str(journal["safety_backup"])
        if safety.exists():
            found.append(safety)
    return tuple(found)


def _recover(paths: AppPaths, journal: dict[str, object]) -> RestoreRecovery:
    stage = journal.get("stage")
    live = paths.database_file
    temp = paths.database_dir / str(journal.get("temp"))
    backup = str(journal.get("backup") or "")
    live_existed = bool(journal.get("live_existed"))
    removed: list[Path] = []

    def rollback(detail: str) -> RestoreRecovery:
        if temp.exists():
            temp.unlink()
            removed.append(temp)
        if live_existed and live.exists():
            # the displaced link of an unchanged live file is a duplicate of it: drop that link only
            for name in journal.get("displaced") or []:  # type: ignore[union-attr]
                linked = displaced_dir(paths) / str(name)
                duplicate = (
                    str(name).endswith("-displaced.sqlite3")
                    and linked.exists()
                    and sha256_file(linked) == journal.get("original_sha256")
                )
                if duplicate:
                    linked.unlink()
                    removed.append(linked)
        _fsync_dir(paths.database_dir)
        _remove_journal(paths)
        return RestoreRecovery(RecoveryOutcome.ROLLED_BACK, detail, backup, (), tuple(removed))

    def ambiguous(detail: str) -> RestoreRecovery:
        log.error("Interrupted restore cannot be resolved automatically: %s", detail)
        return RestoreRecovery(RecoveryOutcome.AMBIGUOUS, detail, backup, _candidates(paths, journal))

    if stage == "prepared":
        if live_existed and not live.exists():
            return ambiguous("the working database disappeared during the restore")
        return rollback("interrupted before the database was replaced")
    if stage == "displaced":
        if live.exists():
            current = sha256_file(live)
            if live_existed and current == journal.get("original_sha256"):
                return rollback("interrupted before the database was replaced")
            if current == journal.get("temp_sha256"):
                stage = "published"
            elif not live_existed:  # someone else's file appeared; ours was never published
                return rollback("a database appeared before the restored copy was published")
            else:
                return ambiguous("the working database matches neither the original nor the restored copy")
        else:
            if live_existed:
                return ambiguous("the working database is missing after it was set aside")
            return rollback("interrupted before the restored database was published")
    if stage == "published":
        if not live.exists():
            return ambiguous("the restored database is missing")
        try:
            _finish(paths)
        except Exception as exc:  # do not guess: keep everything for the reviewer
            return ambiguous(f"the restored database could not be reopened: {exc}")
        if temp.exists():
            temp.unlink()
            removed.append(temp)
        return RestoreRecovery(
            RecoveryOutcome.COMPLETED, "the restore was completed", backup, (), tuple(removed)
        )
    return ambiguous(f"unknown journal stage {stage!r}")


def recover_interrupted_restore(paths: AppPaths) -> RestoreRecovery:
    """Run at start-up before the database is opened: finish or undo an interrupted restore."""
    path = journal_path(paths)
    if not path.exists():
        removed = tuple(stray_temporary_files(paths))
        for stray in removed:  # never published, never referenced by a journal
            stray.unlink()
        return RestoreRecovery(RecoveryOutcome.NONE, removed=removed)
    journal = _read_journal(paths)
    if journal is None:
        return RestoreRecovery(
            RecoveryOutcome.AMBIGUOUS, "the restore journal is unreadable", None, _candidates(paths, None)
        )
    result = _recover(paths, journal)
    log.warning("Interrupted restore of %s: %s (%s)", result.backup, result.outcome, result.detail)
    return result
