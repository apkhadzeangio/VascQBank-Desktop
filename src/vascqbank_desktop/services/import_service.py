"""Two-stage question import.

Stage A — :func:`dry_run`: discover, parse and validate the selected files and
compare them with the database **without writing anything**.

Stage B — :func:`commit_import`: only after the user confirms. Re-checks that
the source files are byte-identical to what was validated, creates and
verifies a backup, then inserts the eligible new questions in one
transaction. Existing questions — edited or not — are never modified.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

from vascqbank_desktop import __version__
from vascqbank_desktop.config.paths import AppPaths
from vascqbank_desktop.db import question_repository as repo
from vascqbank_desktop.db.backup import BackupInfo, create_backup, prune_automatic_backups
from vascqbank_desktop.db.connection import make_durable, open_database
from vascqbank_desktop.db.migrations import LATEST_VERSION, get_schema_version, read_metadata, utc_now_iso
from vascqbank_desktop.domain.question import ImportedQuestion, SourceKind
from vascqbank_desktop.importexport.common import ParsedItem, SourceFile
from vascqbank_desktop.importexport.discovery import Discovery, discover
from vascqbank_desktop.validation.issues import IssueCode, Severity, ValidationIssue, make_issue
from vascqbank_desktop.validation.question_rules import field_content_errors, normalized_key

log = logging.getLogger(__name__)

IMPORT_AUTHOR = "იმპორტი"


class Outcome(StrEnum):
    NEW = "NEW"
    IDENTICAL_EXISTING = "IDENTICAL_EXISTING"
    CONFLICT_EXISTING = "CONFLICT_EXISTING"
    DUPLICATE_IN_SELECTION = "DUPLICATE_IN_SELECTION"
    INVALID = "INVALID"


@dataclass(slots=True)
class Candidate:
    uid: str | None
    location: str
    outcome: Outcome
    question: ImportedQuestion | None
    issues: list[ValidationIssue] = field(default_factory=list)
    existing_has_edits: bool = False


@dataclass(frozen=True, slots=True)
class SourceSummary:
    kind: SourceKind
    label: str
    files: tuple[SourceFile, ...]
    parsed: int
    valid: int


@dataclass(frozen=True, slots=True)
class ImportCounts:
    total_parsed: int
    valid: int
    invalid: int
    errors: int
    warnings: int
    identical_existing: int
    conflicts: int
    duplicates_in_selection: int
    eligible: int


@dataclass(slots=True)
class DryRunReport:
    created_at: str
    selected_paths: tuple[Path, ...]
    sources: list[SourceSummary]
    candidates: list[Candidate]
    general_issues: list[ValidationIssue]
    database_path: Path
    duration_s: float = 0.0

    @property
    def eligible(self) -> list[ImportedQuestion]:
        return [c.question for c in self.candidates if c.outcome is Outcome.NEW and c.question]

    def all_issues(self) -> list[ValidationIssue]:
        issues = list(self.general_issues)
        for candidate in self.candidates:
            issues.extend(candidate.issues)
        return issues

    @property
    def counts(self) -> ImportCounts:
        issues = self.all_issues()
        by_outcome: dict[Outcome, int] = defaultdict(int)
        for c in self.candidates:
            by_outcome[c.outcome] += 1
        return ImportCounts(
            total_parsed=len(self.candidates),
            # structurally valid; duplicates are counted separately below
            valid=len(self.candidates) - by_outcome[Outcome.INVALID],
            invalid=by_outcome[Outcome.INVALID],
            errors=sum(1 for i in issues if i.severity is Severity.ERROR),
            warnings=sum(1 for i in issues if i.severity is Severity.WARNING),
            identical_existing=by_outcome[Outcome.IDENTICAL_EXISTING],
            conflicts=by_outcome[Outcome.CONFLICT_EXISTING],
            duplicates_in_selection=by_outcome[Outcome.DUPLICATE_IN_SELECTION],
            eligible=by_outcome[Outcome.NEW],
        )

    def source_files(self) -> list[SourceFile]:
        return [f for s in self.sources for f in s.files]


@dataclass(frozen=True, slots=True)
class ImportResult:
    batch_id: int
    created: tuple[str, ...]
    skipped_identical: int
    skipped_conflicts: int
    skipped_existing_at_commit: int
    invalid: int
    warnings: int
    backup: BackupInfo
    duration_s: float


class ImportAbortedError(Exception):
    """The confirmed import did not run; the database is unchanged."""


# --------------------------------------------------------------------------
# Stage A
# --------------------------------------------------------------------------
def discover_selection(selected: Sequence[Path]) -> Discovery:
    """Which sources the selected files/folders form (no parsing, no database)."""
    return discover(selected)


def dry_run(paths: AppPaths, selected: Sequence[Path]) -> DryRunReport:
    started = time.perf_counter()
    discovery = discover(selected)
    general: list[ValidationIssue] = list(discovery.issues)
    sources: list[SourceSummary] = []
    items: list[ParsedItem] = []
    for source in discovery.sources:
        result = source.parse()
        general.extend(result.issues)
        for item in result.items:  # Phase 9b: never "valid" what the editor and approval would refuse
            if item.is_valid and item.question is not None:
                item.issues.extend(
                    field_content_errors(
                        qid=str(item.qid), file=item.question.source.file,
                        line=item.question.source.line, content=item.question.content,
                    )
                )  # fmt: skip
        items.extend(result.items)
        sources.append(
            SourceSummary(
                kind=result.kind,
                label=result.label,
                files=tuple(result.files),
                parsed=len(result.items),
                valid=sum(1 for i in result.items if i.is_valid),
            )
        )

    conn = open_database(paths.database_file)
    try:
        if get_schema_version(conn) != LATEST_VERSION:
            raise ImportAbortedError("database schema is not up to date")
        existing = repo.load_existing(conn)
        stems = repo.current_stems(conn)
    finally:
        conn.close()

    candidates = _classify(items, existing)
    _flag_duplicate_stems(candidates, stems)
    return DryRunReport(
        created_at=utc_now_iso(),
        selected_paths=tuple(selected),
        sources=sources,
        candidates=candidates,
        general_issues=general,
        database_path=paths.database_file,
        duration_s=time.perf_counter() - started,
    )


def _classify(
    items: list[ParsedItem], existing: dict[str, repo.ExistingQuestion]
) -> list[Candidate]:
    by_uid: dict[str, list[ParsedItem]] = defaultdict(list)
    for item in items:
        if item.qid is not None:
            by_uid[str(item.qid)].append(item)

    candidates: list[Candidate] = []
    for item in items:
        uid = str(item.qid) if item.qid is not None else None
        candidate = Candidate(uid=uid, location=item.location, outcome=Outcome.INVALID,
                              question=item.question, issues=list(item.issues))
        same = by_uid.get(uid or "", [])
        if uid and len(same) > 1:
            candidate.outcome = Outcome.DUPLICATE_IN_SELECTION
            candidate.issues.append(
                make_issue(IssueCode.DUPLICATE_ID_IN_SELECTION, qid=uid,
                           file=item.location.rsplit(":", 1)[0],
                           where=", ".join(i.location for i in same))
            )
        elif not item.is_valid:
            candidate.outcome = Outcome.INVALID
        elif uid in existing:
            match = existing[uid]
            candidate.existing_has_edits = match.has_reviewer_edits
            assert item.question is not None
            if item.question.fingerprint() == match.source_fingerprint:
                candidate.outcome = Outcome.IDENTICAL_EXISTING
            else:
                candidate.outcome = Outcome.CONFLICT_EXISTING
                candidate.issues.append(
                    make_issue(IssueCode.CONFLICT_EXISTING, qid=uid,
                               file=item.location.rsplit(":", 1)[0])
                )
        else:
            candidate.outcome = Outcome.NEW
        candidates.append(candidate)
    return candidates


def _flag_duplicate_stems(candidates: list[Candidate], existing_stems: list[tuple[str, str]]) -> None:
    owners: dict[str, list[str]] = defaultdict(list)
    for uid, stem in existing_stems:
        owners[normalized_key(stem)].append(uid)
    new = [c for c in candidates if c.outcome is Outcome.NEW and c.question is not None]
    for c in new:
        assert c.question is not None
        owners[normalized_key(c.question.content.stem)].append(c.uid or "")
    for c in new:
        assert c.question is not None
        others = [u for u in owners[normalized_key(c.question.content.stem)] if u != c.uid]
        if others:
            c.issues.append(
                make_issue(IssueCode.DUPLICATE_STEM, qid=c.uid, file=c.question.source.file,
                           line=c.question.source.line, other=", ".join(others[:3]))
            )


# --------------------------------------------------------------------------
# Stage B
# --------------------------------------------------------------------------
def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(report: DryRunReport, backup: BackupInfo, database_id: str) -> dict[str, object]:
    counts = report.counts
    return {
        "format": "vascqbank-import-manifest/1",
        "app_version": __version__,
        "database_id": database_id,
        "dry_run_at": report.created_at,
        "backup": {"file": backup.path.name, "sha256": backup.sha256},
        "sources": [
            {
                "kind": s.kind.value,
                "label": s.label,
                "files": [{"name": f.name, "path": str(f.path), "sha256": f.sha256, "size": f.size}
                          for f in s.files],
                "parsed": s.parsed,
                "valid": s.valid,
            }
            for s in report.sources
        ],
        "counts": asdict(counts),
        "questions": [
            {"uid": c.uid, "location": c.location, "outcome": c.outcome.value,
             "fingerprint": c.question.fingerprint() if c.question else None}
            for c in report.candidates
        ],
        "issues": [i.as_dict() for i in report.all_issues()],
    }


def commit_import(
    paths: AppPaths,
    report: DryRunReport,
    *,
    author: str = IMPORT_AUTHOR,
    progress: Callable[[str], None] | None = None,
) -> ImportResult:
    """Run the confirmed import described by *report*."""
    started = time.perf_counter()
    started_at = utc_now_iso()
    notify = progress or (lambda _msg: None)
    eligible = report.eligible
    if not eligible:
        raise ImportAbortedError("nothing to import")

    notify("verify-sources")
    for source_file in report.source_files():
        try:
            unchanged = _sha256(source_file.path) == source_file.sha256
        except OSError as exc:
            raise ImportAbortedError(f"source file unreadable: {source_file.path}: {exc}") from exc
        if not unchanged:
            raise ImportAbortedError(f"source file changed after validation: {source_file.path}")

    # Phase 9: the import commit is synced like reviewer work (synchronous=FULL), so a confirmed
    # import cannot silently vanish if the computer loses power right after it was reported done.
    conn = make_durable(open_database(paths.database_file))
    try:
        if get_schema_version(conn) != LATEST_VERSION:
            raise ImportAbortedError("database schema is not up to date")
        notify("backup")
        backup = create_backup(conn, paths.backups_dir, "pre-import")
        prune_automatic_backups(paths.backups_dir)
        notify("insert")
        counts = report.counts
        batch = repo.BatchRecord(
            started_at=started_at,
            finished_at=utc_now_iso(),
            source_summary="; ".join(f"{s.kind.value}:{s.label}" for s in report.sources),
            skipped_identical_count=counts.identical_existing,
            skipped_conflict_count=counts.conflicts,
            invalid_count=counts.invalid,
            warning_count=counts.warnings,
            backup_file=backup.path.name,
            manifest=_manifest(report, backup, read_metadata(conn).get("database_id", "")),
        )
        outcome = repo.insert_import(conn, eligible, batch, author=author, now=utc_now_iso())
    finally:
        conn.close()

    duration = time.perf_counter() - started
    log.info("Import batch %s: %s created, %s skipped at commit, backup %s (%.2fs)",
             outcome.batch_id, len(outcome.created), len(outcome.skipped_existing),
             backup.path.name, duration)
    return ImportResult(
        batch_id=outcome.batch_id,
        created=outcome.created,
        skipped_identical=counts.identical_existing,
        skipped_conflicts=counts.conflicts,
        skipped_existing_at_commit=len(outcome.skipped_existing),
        invalid=counts.invalid,
        warnings=counts.warnings,
        backup=backup,
        duration_s=duration,
    )
