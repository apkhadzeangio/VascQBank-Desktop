"""Read-only comparison of a proposed question source with the working database (Phase 8).

A reviewer may want to know what a newer seed, or a new set of TXT source files, would change —
*without* changing anything. Both the working database and the proposed source are only read:

* **TXT sources** go through the importer's own dry run (:func:`import_service.dry_run`), so they are
  parsed and validated exactly as an import would, and classified with the importer's source
  fingerprints;
* **a seed folder** is verified like at first launch (:func:`seed_service.verify_seed`) and its
  database is opened read-only; its questions' source fingerprints are compared by question ID.

Every question ID ends up in one class: ``NEW`` (only in the proposal), ``IDENTICAL`` (same source
text), ``CONFLICT`` (same ID, different source text) or ``MISSING`` (only in the working database).
Conflicts also say whether the working question carries reviewer edits or a decision — those are
never replaced. There is deliberately no "merge" here: adding new questions stays the ordinary,
explicit import, which skips identical and conflicting questions.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import question_repository as repo
from vascqbank_desktop.db.connection import open_database
from vascqbank_desktop.domain.review_status import ReviewStatus
from vascqbank_desktop.services import import_service
from vascqbank_desktop.services.import_service import Outcome
from vascqbank_desktop.services.seed_service import verify_seed


class Difference(StrEnum):
    NEW = "NEW"
    IDENTICAL = "IDENTICAL"
    CONFLICT = "CONFLICT"
    MISSING = "MISSING"


@dataclass(frozen=True, slots=True)
class ConflictInfo:
    uid: str
    working_status: ReviewStatus
    reviewer_edits: bool  # the working question has saved edits beyond the imported text

    @property
    def reviewed(self) -> bool:
        return self.reviewer_edits or self.working_status is not ReviewStatus.PENDING_REVIEW


@dataclass(frozen=True, slots=True)
class ComparisonReport:
    source_label: str
    new: tuple[str, ...]
    identical: tuple[str, ...]
    conflicts: tuple[ConflictInfo, ...]
    missing: tuple[str, ...]
    invalid: int = 0  # proposed items that failed validation (TXT only)
    duplicates: int = 0  # IDs given more than once in the proposal (TXT only)
    duration_s: float = 0.0
    notes: tuple[str, ...] = field(default_factory=tuple)

    def ids(self, kind: Difference) -> tuple[str, ...]:
        if kind is Difference.CONFLICT:
            return tuple(c.uid for c in self.conflicts)
        return {
            Difference.NEW: self.new,
            Difference.IDENTICAL: self.identical,
            Difference.MISSING: self.missing,
        }[kind]

    @property
    def counts(self) -> dict[Difference, int]:
        return {kind: len(self.ids(kind)) for kind in Difference}


def _working(paths: AppPaths) -> dict[str, repo.ExistingQuestion]:
    conn = open_database(paths.database_file)
    try:
        conn.execute("PRAGMA query_only = ON")
        return repo.load_existing(conn)
    finally:
        conn.close()


def _sorted(uids: object) -> tuple[str, ...]:
    return tuple(sorted(uids))  # type: ignore[call-overload]


def _conflict(existing: repo.ExistingQuestion) -> ConflictInfo:
    return ConflictInfo(existing.uid, existing.status, existing.has_reviewer_edits)


def compare_sources(paths: AppPaths, selected: Sequence[Path]) -> ComparisonReport:
    """Compare TXT source files/folders with the working database (nothing is imported)."""
    started = time.perf_counter()
    report = import_service.dry_run(paths, selected)
    working = _working(paths)
    by_outcome: dict[Outcome, set[str]] = {outcome: set() for outcome in Outcome}
    for candidate in report.candidates:
        if candidate.uid is not None:
            by_outcome[candidate.outcome].add(candidate.uid)
    proposed = {c.uid for c in report.candidates if c.uid is not None}
    return ComparisonReport(
        source_label=", ".join(s.label for s in report.sources) or ", ".join(str(p) for p in selected),
        new=_sorted(by_outcome[Outcome.NEW]),
        identical=_sorted(by_outcome[Outcome.IDENTICAL_EXISTING]),
        conflicts=tuple(_conflict(working[uid]) for uid in _sorted(by_outcome[Outcome.CONFLICT_EXISTING])),
        missing=_sorted(set(working) - proposed),
        invalid=sum(1 for c in report.candidates if c.outcome is Outcome.INVALID),
        duplicates=len(by_outcome[Outcome.DUPLICATE_IN_SELECTION]),
        duration_s=time.perf_counter() - started,
    )


def compare_seed(paths: AppPaths, seed_dir: Path) -> ComparisonReport:
    """Compare a seed folder with the working database. Raises :class:`SeedInvalid` for a seed that
    fails verification (then nothing is compared)."""
    started = time.perf_counter()
    seed = verify_seed(seed_dir)
    uri = seed.database_path.resolve().as_uri() + "?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        proposed = repo.load_existing(conn)
    finally:
        conn.close()
    working = _working(paths)
    common = set(proposed) & set(working)
    identical = {u for u in common if proposed[u].source_fingerprint == working[u].source_fingerprint}
    return ComparisonReport(
        source_label=f"{seed.manifest.kind.value} {seed.manifest.seed_version}",
        new=_sorted(set(proposed) - set(working)),
        identical=_sorted(identical),
        conflicts=tuple(_conflict(working[u]) for u in _sorted(common - identical)),
        missing=_sorted(set(working) - set(proposed)),
        duration_s=time.perf_counter() - started,
    )
