"""Portable backups (Phase 9): save a verified backup elsewhere, bring an external one back in.

*Save copy* writes an already verified backup and its manifest to a folder the reviewer chooses
(typically a USB drive). The live database is never copied — only finished, self-contained backup
files (rollback-journal mode, no ``-wal`` side file). Nothing is ever overwritten and the original
backup is never touched.

*Import* copies an externally stored backup into the application's ``backups`` folder under a new
``…-imported.sqlite3`` name, so the ordinary, journaled restore (:mod:`restore_service`) can use
it. The external file is only read.

Both directions follow the same protocol:

1. the source is verified (manifest size + SHA-256, ``integrity_check``, row counts);
2. bytes are streamed into a hidden ``.<name>.<uuid>.partial`` file opened with ``O_EXCL``,
   hashed on the way and ``fsync``-ed; the hash must equal the manifest's;
3. the file is published under its final name **without overwriting** (hard link where the
   file system supports it, otherwise a rename guarded by an existence check — FAT/exFAT have no
   hard links; that fallback has a tiny race window documented in ARCHITECTURE.md §36);
4. the published copy is read back from the destination and verified again.

On any failure every file this call created is removed; nothing else is ever deleted.
"""

from __future__ import annotations

import errno
import hashlib
import json
import logging
import os
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import backup as backup_db
from vascqbank_desktop.services.restore_service import RestoreRefused, inspect_backup

__all__ = [
    "IMPORTED_REASON",
    "BackupCopyError",
    "BackupCopyResult",
    "CopyProblem",
    "import_external_backup",
    "save_backup_copy",
]

log = logging.getLogger(__name__)

IMPORTED_REASON = "imported"
_CHUNK = 1 << 20


class CopyProblem(StrEnum):
    SOURCE_INVALID = "SOURCE_INVALID"  # the backup to copy does not verify (or has no manifest)
    DESTINATION_MISSING = "DESTINATION_MISSING"  # the chosen folder does not exist
    DESTINATION_EXISTS = "DESTINATION_EXISTS"  # a file with that name is already there
    NOT_WRITABLE = "NOT_WRITABLE"  # permission denied / read-only medium
    DISK_FULL = "DISK_FULL"
    VERIFY_FAILED = "VERIFY_FAILED"  # the written copy does not match the original
    WRITE_FAILED = "WRITE_FAILED"  # any other I/O error (drive removed, …)


class BackupCopyError(Exception):
    """The copy did not happen; nothing this call wrote was left behind."""

    def __init__(self, problem: CopyProblem, detail: str = "") -> None:
        super().__init__(f"{problem.value}: {detail}" if detail else problem.value)
        self.problem = problem
        self.detail = detail


@dataclass(frozen=True, slots=True)
class BackupCopyResult:
    backup: Path
    manifest: Path
    sha256: str
    size: int
    already_present: bool = False  # import only: an identical backup was already in the folder


def _io_problem(exc: OSError) -> CopyProblem:
    if exc.errno == errno.ENOSPC:
        return CopyProblem.DISK_FULL
    if isinstance(exc, PermissionError) or exc.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
        return CopyProblem.NOT_WRITABLE
    if isinstance(exc, FileExistsError):
        return CopyProblem.DESTINATION_EXISTS
    return CopyProblem.WRITE_FAILED


def _fsync_dir(path: Path) -> None:
    if os.name == "nt":
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:  # some removable-media drivers refuse; the file itself was fsync-ed
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_CHUNK), b""):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _write_temp(destination: Path, chunks_from: Path | bytes) -> tuple[Path, str, int]:
    """Stream *chunks_from* into a fresh hidden temp file beside *destination*; fsync it."""
    temp = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.partial")
    digest = hashlib.sha256()
    size = 0
    try:
        with temp.open("xb") as out:
            if isinstance(chunks_from, bytes):
                out.write(chunks_from)
                digest.update(chunks_from)
                size = len(chunks_from)
            else:
                with chunks_from.open("rb") as src:
                    for block in iter(lambda: src.read(_CHUNK), b""):
                        out.write(block)
                        digest.update(block)
                        size += len(block)
            out.flush()
            os.fsync(out.fileno())
    except BaseException:
        temp.unlink(missing_ok=True)
        raise
    return temp, digest.hexdigest(), size


def _publish_no_overwrite(temp: Path, final: Path) -> None:
    """Give *temp* the name *final*; raise FileExistsError rather than replace anything."""
    try:
        os.link(temp, final)  # atomic and fails if final exists
    except FileExistsError:
        raise
    except OSError:
        # No hard links here (FAT/exFAT, some network shares). On Windows ``os.rename`` refuses to
        # replace; on POSIX it would replace, hence the explicit check first.
        if final.exists() or final.is_symlink():
            raise FileExistsError(errno.EEXIST, "destination exists", str(final)) from None
        os.rename(temp, final)
        return
    # the temporary name is removed by the caller (failing to remove it does not undo the publication)


class _Created:
    """Remembers the files this call created so that a failure removes exactly those."""

    def __init__(self) -> None:
        self.files: list[Path] = []

    def remove_all(self) -> None:
        for path in reversed(self.files):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                log.warning("Could not remove %s after a failed copy", path)


def _copy_file(source: Path | bytes, final: Path, expected_sha: str, created: _Created) -> int:
    temp, digest, size = _write_temp(final, source)
    try:
        if digest != expected_sha:
            raise BackupCopyError(CopyProblem.VERIFY_FAILED, f"{final.name}: SHA-256 differs while copying")
        _publish_no_overwrite(temp, final)
        # Phase 9b: registered the moment it exists, so a later failure removes it again (before, a
        # failing removal of the temp file left the published file behind, unregistered)
        created.files.append(final)
    finally:
        _remove_temp(temp)
    return size


def _remove_temp(temp: Path, attempts: int = 3) -> None:
    """Remove a temporary file; retried briefly (on Windows an antivirus scanner may hold a file it
    has just seen being written). A temp file that still cannot be removed is only clutter."""
    for attempt in range(1, attempts + 1):
        try:
            temp.unlink(missing_ok=True)
            return
        except OSError:
            if attempt == attempts:
                log.warning("Could not remove the temporary file %s", temp)
                return
            time.sleep(0.05 * attempt)


def _verified_source(source: Path) -> tuple[dict[str, object], bytes]:
    if not source.is_file():
        raise BackupCopyError(CopyProblem.SOURCE_INVALID, f"{source.name} does not exist")
    manifest_path = source.with_name(source.name + ".json")
    check = backup_db.check_backup_file(source)
    manifest = backup_db.read_manifest(source)
    if not check.ok or manifest is None:
        detail = check.message or (check.problem.value if not check.ok else "manifest is missing")
        raise BackupCopyError(CopyProblem.SOURCE_INVALID, f"{source.name}: {detail}")
    try:
        manifest_bytes = manifest_path.read_bytes()
    except OSError as exc:
        raise BackupCopyError(CopyProblem.SOURCE_INVALID, f"{manifest_path.name}: {exc}") from exc
    return manifest, manifest_bytes


def save_backup_copy(source: Path, destination_dir: Path) -> BackupCopyResult:
    """Copy the verified backup *source* and its manifest into *destination_dir*.

    The copy keeps the original file names, so it is recognisable and can later be brought back
    with :func:`import_external_backup`. Raises :class:`BackupCopyError`.
    """
    manifest, manifest_bytes = _verified_source(source)
    expected_sha = str(manifest["sha256"])
    if not destination_dir.is_dir():
        raise BackupCopyError(CopyProblem.DESTINATION_MISSING, str(destination_dir))
    target = destination_dir / source.name
    target_manifest = destination_dir / (source.name + ".json")
    if source.resolve().parent == destination_dir.resolve():
        raise BackupCopyError(CopyProblem.DESTINATION_EXISTS, f"{source.name} is already in this folder")
    for path in (target, target_manifest):
        if path.exists() or path.is_symlink():
            raise BackupCopyError(CopyProblem.DESTINATION_EXISTS, path.name)

    created = _Created()
    try:
        size = _copy_file(source, target, expected_sha, created)
        manifest_sha = hashlib.sha256(manifest_bytes).hexdigest()
        _copy_file(manifest_bytes, target_manifest, manifest_sha, created)
        _fsync_dir(destination_dir)
        # Read back what the medium actually stored, then the full backup verification.
        written_sha, written_size = _sha256_file(target)
        if (written_sha, written_size) != (expected_sha, size):
            raise BackupCopyError(CopyProblem.VERIFY_FAILED, f"{target.name}: read-back differs")
        check = backup_db.check_backup_file(target)
        if not check.ok:
            raise BackupCopyError(CopyProblem.VERIFY_FAILED, f"{target.name}: {check.message}")
    except BackupCopyError:
        created.remove_all()
        raise
    except OSError as exc:
        created.remove_all()
        raise BackupCopyError(_io_problem(exc), str(exc)) from exc
    except BaseException:
        created.remove_all()
        raise
    log.info("Backup copy saved: %s", target.name)
    return BackupCopyResult(target, target_manifest, expected_sha, size)


def _existing_identical(paths: AppPaths, sha256: str, size: int) -> Path | None:
    for candidate in backup_db.list_backups(paths.backups_dir):
        manifest = backup_db.read_manifest(candidate)
        if not manifest or manifest.get("sha256") != sha256 or manifest.get("size") != size:
            continue
        if backup_db.check_backup_file(candidate).ok:
            return candidate
    return None


def import_external_backup(paths: AppPaths, source: Path, *, now: datetime | None = None) -> BackupCopyResult:
    """Copy an external verified backup into the backups folder so that it can be restored.

    The copy is named ``vascqbank-<now>-imported.sqlite3``; its manifest is the original one plus
    ``imported_from`` (file name only), ``imported_at`` and ``original_reason``. An identical
    backup already in the folder is returned instead of a duplicate. Raises :class:`BackupCopyError`.
    """
    if not source.is_file():
        raise BackupCopyError(CopyProblem.SOURCE_INVALID, f"{source.name} does not exist")
    paths.backups_dir.mkdir(parents=True, exist_ok=True)
    if source.resolve().parent == paths.backups_dir.resolve():
        manifest = backup_db.read_manifest(source) or {}
        if backup_db.check_backup_file(source).ok and manifest:
            return BackupCopyResult(
                source,
                source.with_name(source.name + ".json"),
                str(manifest.get("sha256")),
                source.stat().st_size,
                already_present=True,
            )
    try:
        summary = inspect_backup(source)  # the same checks a restore applies
    except RestoreRefused as exc:
        raise BackupCopyError(CopyProblem.SOURCE_INVALID, f"{source.name}: {exc}") from exc
    except OSError as exc:
        raise BackupCopyError(CopyProblem.SOURCE_INVALID, f"{source.name}: {exc}") from exc
    manifest, _ = _verified_source(source)

    existing = _existing_identical(paths, summary.sha256, summary.size)
    if existing is not None:
        return BackupCopyResult(
            existing, existing.with_name(existing.name + ".json"), summary.sha256, summary.size, True
        )

    moment = now or datetime.now(UTC)
    final = paths.backups_dir / f"vascqbank-{moment.strftime('%Y%m%d-%H%M%S-%f')}-{IMPORTED_REASON}.sqlite3"
    final_manifest = final.with_name(final.name + ".json")
    new_manifest = dict(manifest)
    new_manifest.update(
        {
            "path": final.name,
            "manifest_path": final_manifest.name,
            "reason": IMPORTED_REASON,
            "original_reason": manifest.get("reason"),
            "imported_from": source.name,  # name only: an external path can reveal a user name
            "imported_at": moment.replace(microsecond=0).isoformat(),
        }
    )
    created = _Created()
    try:
        if final.exists() or final_manifest.exists():
            raise BackupCopyError(CopyProblem.DESTINATION_EXISTS, final.name)
        # Manifest first: a backup file must never be visible without it (as in create_backup).
        data = json.dumps(new_manifest, ensure_ascii=False, indent=2).encode("utf-8")
        _copy_file(data, final_manifest, hashlib.sha256(data).hexdigest(), created)
        size = _copy_file(source, final, summary.sha256, created)
        _fsync_dir(paths.backups_dir)
        check = backup_db.check_backup_file(final)
        if not check.ok:
            raise BackupCopyError(CopyProblem.VERIFY_FAILED, f"{final.name}: {check.message}")
    except BackupCopyError:
        created.remove_all()
        raise
    except OSError as exc:
        created.remove_all()
        raise BackupCopyError(_io_problem(exc), str(exc)) from exc
    except BaseException:
        created.remove_all()
        raise
    log.info("External backup imported as %s", final.name)
    return BackupCopyResult(final, final_manifest, summary.sha256, size)
