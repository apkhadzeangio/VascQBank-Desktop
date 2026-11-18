"""What happens at start-up before any window opens: open, install from the seed, or stop.

:func:`prepare_storage` decides — in this order, and never destructively:

0. An interrupted backup restore is finished or undone (``restore_service``); if that cannot be proven
   safe, :attr:`Outcome.RESTORE_INTERRUPTED` — nothing is opened or created and the reviewer decides.
1. Temporary files of an interrupted seed installation are removed (they are never a published
   database).
2. **A working database exists** → it is opened and migrated as always (``initialize_storage``).
   The seed is ignored: an application update never resets, replaces or merges reviewer work.
3. **No database, but traces of an earlier one** — backups, ``-wal``/``-shm``/``-journal`` files,
   the derived search index, published exports, or ``settings.json`` → :attr:`Outcome.RECOVERY_NEEDED`.
   Nothing is created, so the evidence stays exactly as it is; the user is told and decides. Only an
   explicit ``allow_new_database=True`` (after confirmation) continues — backups are never touched.
4. **No database, a seed is available** → strict verification, then installation. A seed that fails
   verification gives :attr:`Outcome.SEED_INVALID` and **no** database is created, so a corrected
   application can still install a good seed later.
5. **No database, no seed** → an empty database is created as before (manual TXT import).

Phase 9b — a working database that exists but is **damaged** is never opened silently and never
replaced: :attr:`Outcome.DATABASE_DAMAGED` (nothing touched) when it is a zero-byte file while traces
of earlier work exist (:attr:`DamageKind.EMPTY`), when SQLite reports it corrupt or not a database
(:attr:`DamageKind.CORRUPT`), or when it opens but ``quick_check`` fails
(:attr:`DamageKind.CHECK_FAILED`). The reviewer then chooses: restore a backup (the damaged file is set
aside into ``displaced/``, never deleted), open it anyway (``accept_damaged``, only if it opens), a new
empty bank (only for an empty file, ``allow_new_database``), or quit. "Cannot open" errors that may be
transient (locked, permission, I/O) are *not* damage: they stop the start as before.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db.backup import list_backups
from vascqbank_desktop.services.restore_service import (
    RecoveryOutcome,
    RestoreRecovery,
    displaced_dir,
    recover_interrupted_restore,
)
from vascqbank_desktop.services.seed_service import (
    DatabaseAlreadyExists,
    SeedInstallResult,
    SeedInvalid,
    cleanup_interrupted_installs,
    install_seed,
    verify_seed,
)
from vascqbank_desktop.services.storage import StorageStatus, initialize_storage

log = logging.getLogger(__name__)


class Outcome(StrEnum):
    READY = "READY"
    RECOVERY_NEEDED = "RECOVERY_NEEDED"
    SEED_INVALID = "SEED_INVALID"
    RESTORE_INTERRUPTED = "RESTORE_INTERRUPTED"  # a restore that cannot be finished or undone safely
    DATABASE_DAMAGED = "DATABASE_DAMAGED"  # Phase 9b: the working database is empty/corrupt; untouched


class DamageKind(StrEnum):
    EMPTY = "EMPTY"  # a zero-byte file although earlier work exists (backups, exports, settings, …)
    CORRUPT = "CORRUPT"  # SQLite: "database disk image is malformed" / "file is not a database"
    CHECK_FAILED = "CHECK_FAILED"  # it opens, but PRAGMA quick_check reports problems


class EvidenceKind(StrEnum):
    BACKUPS = "BACKUPS"
    DATABASE_SIDE_FILES = "DATABASE_SIDE_FILES"
    SEARCH_INDEX = "SEARCH_INDEX"
    EXPORTS = "EXPORTS"
    SETTINGS = "SETTINGS"
    DISPLACED = "DISPLACED"  # databases set aside by a restore


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: EvidenceKind
    paths: tuple[Path, ...]


@dataclass(frozen=True, slots=True)
class StartupResult:
    outcome: Outcome
    status: StorageStatus | None = None
    seed_install: SeedInstallResult | None = None
    evidence: tuple[Evidence, ...] = ()
    seed_problem: SeedInvalid | None = None
    cleaned: tuple[Path, ...] = field(default_factory=tuple)
    restore_recovery: RestoreRecovery | None = None  # an interrupted restore found at start-up
    damage: DamageKind | None = None  # Outcome.DATABASE_DAMAGED only
    damage_detail: str = ""


# SQLITE_CORRUPT (11) and SQLITE_NOTADB (26), including their extended codes. Locked, busy, I/O,
# permission and "cannot open" errors are deliberately excluded: they can be transient.
_CORRUPTION_CODES = (11, 26)


def is_corruption(exc: BaseException) -> bool:
    code = getattr(exc, "sqlite_errorcode", None)
    if not isinstance(exc, sqlite3.DatabaseError) or not isinstance(code, int):
        return False
    return code & 0xFF in _CORRUPTION_CODES


def _empty_leftover(paths: AppPaths) -> bool:
    """A zero-byte database file without side files: it holds nothing at all."""
    database = paths.database_file
    return (
        database.is_file()
        and database.stat().st_size == 0
        and not any(Path(f"{database}{s}").exists() for s in ("-wal", "-journal"))
    )


def recovery_evidence(paths: AppPaths) -> tuple[Evidence, ...]:
    """Traces of a working database that is no longer where it should be."""
    found: list[Evidence] = []
    backups = list_backups(paths.backups_dir) if paths.backups_dir.is_dir() else []
    if backups:
        found.append(Evidence(EvidenceKind.BACKUPS, tuple(backups)))
    side = tuple(
        p for p in (Path(f"{paths.database_file}{s}") for s in ("-wal", "-shm", "-journal")) if p.exists()
    )
    if side:
        found.append(Evidence(EvidenceKind.DATABASE_SIDE_FILES, side))
    if paths.search_index_file.exists():
        found.append(Evidence(EvidenceKind.SEARCH_INDEX, (paths.search_index_file,)))
    if paths.exports_dir.is_dir():
        exports = tuple(sorted(p for p in paths.exports_dir.glob("VascQBank_Export_*") if p.is_dir()))
        if exports:
            found.append(Evidence(EvidenceKind.EXPORTS, exports))
    if paths.settings_file.exists():
        found.append(Evidence(EvidenceKind.SETTINGS, (paths.settings_file,)))
    displaced = displaced_dir(paths)
    if displaced.is_dir():
        kept = tuple(sorted(p for p in displaced.iterdir() if p.is_file()))
        if kept:
            found.append(Evidence(EvidenceKind.DISPLACED, kept))
    return tuple(found)


def prepare_storage(
    paths: AppPaths,
    seed_dir: Path | None,
    *,
    allow_new_database: bool = False,
    accept_damaged: bool = False,
) -> StartupResult:
    paths.ensure_directories()
    # An interrupted restore is resolved first, before anything opens or creates a database.
    recovery = recover_interrupted_restore(paths)
    if recovery.outcome is RecoveryOutcome.AMBIGUOUS:
        return StartupResult(Outcome.RESTORE_INTERRUPTED, restore_recovery=recovery)
    restored = recovery if recovery.outcome is not RecoveryOutcome.NONE else None
    cleaned = tuple(cleanup_interrupted_installs(paths))
    if cleaned:
        log.warning("Removed %s file(s) of an interrupted seed installation", len(cleaned))
    if _empty_leftover(paths):
        evidence = recovery_evidence(paths)
        if evidence and not allow_new_database:
            log.error("The working database %s is empty although earlier work exists", paths.database_file)
            return StartupResult(
                Outcome.DATABASE_DAMAGED,
                evidence=evidence,
                cleaned=cleaned,
                restore_recovery=restored,
                damage=DamageKind.EMPTY,
                damage_detail="zero-byte database file",
            )
        if not evidence and seed_dir is not None:
            # a first launch that died before writing anything: the empty file holds nothing, and
            # leaving it would make the seed look already installed
            paths.database_file.unlink()
            Path(f"{paths.database_file}-shm").unlink(missing_ok=True)
            log.warning("Removed an empty database file left by an interrupted first start")
    if paths.database_file.exists():
        try:
            status = initialize_storage(paths)
        except sqlite3.DatabaseError as exc:
            if not is_corruption(exc):
                raise
            log.error("The working database %s is damaged: %s", paths.database_file, exc)
            return StartupResult(
                Outcome.DATABASE_DAMAGED,
                evidence=recovery_evidence(paths),
                cleaned=cleaned,
                restore_recovery=restored,
                damage=DamageKind.CORRUPT,
                damage_detail=f"{type(exc).__name__}: {exc}",
            )
        if not status.integrity_ok and not accept_damaged:
            return StartupResult(
                Outcome.DATABASE_DAMAGED,
                status=status,
                evidence=recovery_evidence(paths),
                cleaned=cleaned,
                restore_recovery=restored,
                damage=DamageKind.CHECK_FAILED,
                damage_detail="PRAGMA quick_check reported problems",
            )
        return StartupResult(Outcome.READY, status, cleaned=cleaned, restore_recovery=restored)

    evidence = recovery_evidence(paths)
    if evidence and not allow_new_database:
        log.error(
            "No database at %s but traces of an earlier one exist (%s); nothing is created",
            paths.database_file,
            ", ".join(e.kind.value for e in evidence),
        )
        return StartupResult(
            Outcome.RECOVERY_NEEDED, evidence=evidence, cleaned=cleaned, restore_recovery=restored
        )

    installed: SeedInstallResult | None = None
    if seed_dir is not None:
        try:
            seed = verify_seed(seed_dir)
        except SeedInvalid as exc:
            log.error("The seed in %s failed verification: %s", seed_dir, exc)
            return StartupResult(Outcome.SEED_INVALID, seed_problem=exc, cleaned=cleaned)
        try:
            installed = install_seed(paths, seed)
        except SeedInvalid as exc:  # the copy did not match the verified seed (disk or media error)
            log.error("Installing the seed failed: %s", exc)
            return StartupResult(Outcome.SEED_INVALID, seed_problem=exc, cleaned=cleaned)
        except DatabaseAlreadyExists:  # another instance installed it a moment ago: use that one
            installed = None
    return StartupResult(
        Outcome.READY,
        initialize_storage(paths),
        seed_install=installed,
        cleaned=cleaned,
        restore_recovery=restored,
    )
