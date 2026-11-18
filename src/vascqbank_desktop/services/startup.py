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
"""

from __future__ import annotations

import logging
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
    paths: AppPaths, seed_dir: Path | None, *, allow_new_database: bool = False
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
    if paths.database_file.exists():
        return StartupResult(
            Outcome.READY, initialize_storage(paths), cleaned=cleaned, restore_recovery=restored
        )

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
